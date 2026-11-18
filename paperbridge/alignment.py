"""Paragraph anchors in PDF coordinates (bottom-left origin)."""
import collections
import json
import re
import unicodedata


def pdf_rect(rect, height):
    return [float(rect[0]), height-float(rect[3]), float(rect[2]), height-float(rect[1])]


def fingerprint(doc):
    match = re.search(r'<([0-9A-Fa-f]+)>', doc.xref_get_key(-1, 'ID')[1])
    return match[1].lower() if match else None


def embed(doc, mapping):
    name = 'paperbridge-alignment.json'
    if name in doc.embfile_names():
        doc.embfile_del(name)
    doc.embfile_add(name, json.dumps(mapping, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
                    filename=name, ufilename=name, desc='PaperBridge paragraph correspondence')


def line_rects(page, box):
    import pymupdf as f
    box = f.Rect(box)
    return [line['bbox'] for block in page.get_text('dict')['blocks'] if block.get('type') == 0
            for line in block['lines'] if (f.Rect(line['bbox']) & box).get_area() >
            max(1, f.Rect(line['bbox']).get_area()*.55)]


def facts(text):
    return collections.Counter(re.findall(r'(?<![A-Za-z0-9_])[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?',
                                          unicodedata.normalize('NFKC', text)))
