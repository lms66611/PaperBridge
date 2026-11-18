from decimal import Decimal
from pathlib import Path
import json
import sys
import tempfile

from paperbridge.usage import (
    checkpoint_processing, duration_label, elapsed_seconds, finish_processing,
    job_usage, start_processing,
)

work = Path(__file__).resolve().parent
job = {'id': 'fixture', 'status': 'running', 'segments': [],
       'processing_seconds': 0.0, 'free_usage': []}
start_processing(job, 100)
checkpoint_processing(job, 145)
assert elapsed_seconds(job, 150) == 50
finish_processing(job, 160)
assert elapsed_seconds(job, 1000) == 60
start_processing(job, 200)
finish_processing(job, 230)
assert duration_label(job) == '1分30秒'

old = {'id': 'legacy', 'status': 'failed', 'segments': [{'source': 'abc',
       'translation': '译文', 'engine': 'Zotero/bing+gpt'}], 'gpt_cost': 0.01}
assert duration_label(old) == '未记录'
start_processing(old, 300)
finish_processing(old, 320)
assert duration_label(old) == '20秒*'
with tempfile.TemporaryDirectory(dir=work) as temporary:
    jobs = Path(temporary)
    receipts = jobs / 'fixture' / 'gpt-receipts'
    receipts.mkdir(parents=True)
    (receipts / 'paid.json').write_text(json.dumps({
        'state': 'validated', 'billing': {'input_tokens': 120, 'output_tokens': 40,
                                         'usd': '0.00123'}}), 'utf-8')
    (receipts / 'pending.json').write_text(json.dumps({
        'state': 'pending', 'reserved_usd': 0.005}), 'utf-8')
    job['segments'] = [{'source': 'abcd', 'translation': '译文', 'engine': 'Zotero/bing'},
                       {'source': 'abcd', 'translation': '译文', 'engine': 'local/cpu'}]
    job['free_usage'] = [{'service': 'bing', 'chars': 4, 'segment_id': 'p1'}]
    usage = job_usage(job, jobs)
    assert (usage['free_segments'], usage['free_chars'], usage['free_calls']) == (1, 4, 1)
    assert (usage['paid_calls'], usage['pending_calls']) == (1, 1)
    assert (usage['input_tokens'], usage['output_tokens']) == (120, 40)
    assert usage['paid_usd'] == Decimal('0.00123')
    assert usage['reserved_usd'] == Decimal('0.005')
    assert job_usage(old, jobs)['free_calls'] is None
print('USAGE_OK')
