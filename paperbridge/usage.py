"""Processing time and translation usage derived from durable job records."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import time

from .storage import read_json


def start_processing(job, now=None):
    now = time.time() if now is None else now
    if 'processing_seconds' not in job:
        job['processing_seconds'] = 0.0
        if job.get('segments'):
            job['timing_partial'] = True
    job['processing_checkpoint_at'] = now


def checkpoint_processing(job, now=None):
    started = job.get('processing_checkpoint_at')
    if started is None:
        return
    now = time.time() if now is None else now
    job['processing_seconds'] = float(job.get('processing_seconds', 0)) + max(0, now - started)
    job['processing_checkpoint_at'] = now


def finish_processing(job, now=None):
    checkpoint_processing(job, now)
    job.pop('processing_checkpoint_at', None)


def elapsed_seconds(job, now=None):
    if 'processing_seconds' not in job:
        return None
    now = time.time() if now is None else now
    elapsed = float(job['processing_seconds'])
    if job.get('processing_checkpoint_at') is not None:
        elapsed += max(0, now - job['processing_checkpoint_at'])
    return max(0, elapsed)


def duration_label(job, now=None):
    seconds = elapsed_seconds(job, now)
    if seconds is None or (seconds < 1 and job.get('status') == 'ready'):
        return '未记录' if seconds is None else '—'
    seconds = int(seconds)
    if seconds >= 3600:
        label = f'{seconds // 3600}时{seconds % 3600 // 60}分'
    elif seconds >= 60:
        label = f'{seconds // 60}分{seconds % 60}秒'
    else:
        label = f'{seconds}秒'
    return label + ('*' if job.get('timing_partial') else '')


def _money(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def job_usage(job, jobs_root, now=None):
    segments = [item for item in job.get('segments', []) if
                item.get('translation') and item.get('engine', '').startswith('Zotero/')]
    calls = job.get('free_usage')
    result = {
        'elapsed': elapsed_seconds(job, now),
        'timing_partial': bool(job.get('timing_partial')),
        'free_segments': len(segments),
        'free_chars': sum(len(item.get('source', '')) for item in segments),
        'free_calls': len(calls) if calls is not None else None,
        'free_call_chars': sum(int(call.get('chars', 0)) for call in calls or []),
        'paid_calls': 0,
        'pending_calls': 0,
        'input_tokens': 0,
        'output_tokens': 0,
        'paid_usd': Decimal(0),
        'reserved_usd': Decimal(0),
        'token_history_missing': False,
    }
    receipt_dir = jobs_root / job['id'] / 'gpt-receipts'
    receipts = [read_json(path, {}) for path in receipt_dir.glob('*.json')]
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        billing = receipt.get('billing') or {}
        usage = receipt.get('usage') or {}
        if billing or usage:
            result['paid_calls'] += 1
            result['input_tokens'] += int(billing.get('input_tokens', usage.get('input_tokens', 0)))
            result['output_tokens'] += int(billing.get('output_tokens', usage.get('output_tokens', 0)))
            if billing.get('usd') is not None:
                result['paid_usd'] += _money(billing['usd'])
            else:
                result['reserved_usd'] += _money(receipt.get('reserved_usd'))
        elif receipt.get('state') == 'pending':
            result['pending_calls'] += 1
            result['reserved_usd'] += _money(receipt.get('reserved_usd'))
    if not receipts and job.get('gpt_cost'):
        result['paid_usd'] = _money(job['gpt_cost'])
        result['token_history_missing'] = True
    return result
