"""Revise only verified Chinese paragraphs in an existing side-by-side PDF."""
import hashlib,html,io,json,pathlib,re
from .alignment import embed,pdf_rect,fingerprint,line_rects

def revise_comparison(source_file,baseline_file,pairs,corrections,target,assets):
    import pymupdf as f
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    from reportlab.pdfgen.canvas import Canvas
    from .pipeline import protected_facts
    from .typography import scientific_markup,source_script_terms
    fontfile=next((pathlib.Path(assets)/'fonts').glob('NotoSansSC*.ttf'))
    font='PB-review-'+hashlib.sha256(str(fontfile).encode()).hexdigest()[:8]
    if font not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(font,str(fontfile)))
    doc=f.open(baseline_file);source=f.open(source_file)
    report={'applied':[],'deferred':[],'pages':len(doc),'left_pixel_checks':[]}
    mapping={'version':1,'source_fingerprint':fingerprint(source),'pairs':[]}
    if 'paperbridge-alignment.json' in doc.embfile_names():
        baseline_mapping=json.loads(doc.embfile_get('paperbridge-alignment.json'))
        mapping['citations']=baseline_mapping.get('citations',[])
    prepared={};updated={};placements={}
    try:
        for pair in pairs:
            c=corrections.get(pair['id'])
            if not c:continue
            old=pair['translation'];new=c['translation']
            facts=protected_facts(pair['source'])
            error=lambda t:sum((facts-protected_facts(t)).values())+sum((protected_facts(t)-facts).values())
            if error(new)>error(old):
                report['deferred'].append({'id':pair['id'],'reason':'修订增加数字差异','suggestion':c});continue
            page=doc[pair['page']];width=source[pair['page']].rect.width
            box=f.Rect(pair['target_bbox'])
            sb=f.Rect(pair.get('source_bbox') or box-(width,0,width,0))+(width,0,width,0)
            # Use existing paragraph space, bounded by all adjacent translated text.
            box.x1=max(box.x1,sb.x1);box.y1=max(box.y1,sb.y1)
            others=[f.Rect(p['target_bbox']) for p in pairs if p['page']==pair['page'] and p['id']!=pair['id']]
            lower=[r.y0-.8 for r in others if r.y0>=pair['target_bbox'][3]-.2 and
                   min(r.x1,box.x1)-max(r.x0,box.x0)>3]
            if lower:box.y1=min(box.y1,min(lower))
            box &= page.rect
            fitted=None
            maximum=min(12,max(8.2,float(pair['font_size'])))
            terms=source_script_terms(source[pair['page']],pair.get('source_bbox') or
                                      (box.x0-width,box.y0,box.x1-width,box.y1))
            for size in (maximum,8,7.5,7,6.8):
                if size>maximum+.01:continue
                style=ParagraphStyle('review',fontName=font,fontSize=size,leading=size*1.18,wordWrap='CJK',splitLongWords=True)
                para=Paragraph(scientific_markup(re.sub(r'[\x00-\x1f]',' ',new),font,terms),style)
                if para.wrap(box.width,box.height)[1]<=box.height:
                    fitted=para;break
            if fitted is None:
                report['deferred'].append({'id':pair['id'],'reason':'原位置空间不足，保留原译及待校对建议','suggestion':c});continue
            prepared.setdefault(pair['page'],[]).append((pair,c,box,fitted))
        for pi,entries in prepared.items():
            page=doc[pi];width=source[pi].rect.width
            for pair,c,box,para in entries:
                for rect in pair['target_rects']:
                    r=f.Rect(rect)
                    if r.x0<width:raise ValueError('拒绝修改原文区域')
                    page.add_redact_annot(r,fill=False,cross_out=False)
            page.apply_redactions(images=0,graphics=0,text=0)
            buf=io.BytesIO();canvas=Canvas(buf,pagesize=(page.rect.width,page.rect.height),pageCompression=1)
            for pair,c,box,para in entries:
                para.drawOn(canvas,box.x0,page.rect.height-box.y0-para.height)
                updated[pair['id']]=c['translation']
                placements[pair['id']]=[box.x0,box.y0,box.x1,box.y0+para.height]
                report['applied'].append({'id':pair['id'],'reason':c['reason'],'font_size':para.style.fontSize})
            canvas.save()
            with f.open(stream=buf.getvalue(),filetype='pdf') as overlay:page.show_pdf_page(page.rect,overlay,0)
        for pair in pairs:
            pi=pair['page'];height=doc[pi].rect.height
            tr=line_rects(doc[pi],placements[pair['id']]) if pair['id'] in placements else pair['target_rects']
            mapping['pairs'].append({'id':pair['id'],'text':pair['source'],
                'translation':updated.get(pair['id'],pair['translation']),
                'original':[{'page':pi,'rects':[pdf_rect(r,height) for r in pair['source_rects']]}],
                'source':[{'page':pi,'rects':[pdf_rect(r,height) for r in pair['source_rects']]}],
                'target':[{'page':pi,'rects':[pdf_rect(r,height) for r in tr]}]})
        # PDF redaction can rewrite shared content streams outside a mask.
        # Recompose the left exclusively from an untouched read-only baseline.
        pristine=f.open(baseline_file);composed=f.open()
        try:
            for pi in range(len(doc)):
                page=doc[pi];width=source[pi].rect.width
                spread=composed.new_page(width=page.rect.width,height=page.rect.height)
                left=f.Rect(0,0,width,page.rect.height);right=f.Rect(width,0,page.rect.width,page.rect.height)
                spread.show_pdf_page(left,pristine,pi,clip=left)
                spread.show_pdf_page(right,doc,pi,clip=right)
                if pi==0:
                    # Publication mastheads are bibliographic originals, not body text.
                    # Reuse the clean original instead of the baseline's overlapping translation.
                    top=f.Rect(0,0,width,140)
                    spread.draw_rect(f.Rect(width,0,2*width,140),fill=(1,1,1),color=None,overlay=True)
                    spread.show_pdf_page(f.Rect(width,0,2*width,140),source,pi,clip=top)
                for link in pristine[pi].get_links():
                    spread.insert_link({k:v for k,v in link.items() if k not in ('xref','id','nameddest')})
            doc.close();doc=composed;composed=None
        finally:
            pristine.close()
            if composed is not None:composed.close()
        embed(doc,mapping)
        doc.set_metadata({**doc.metadata,'title':'双语翻译版 · 快速校核','subject':'PaperBridge paragraph alignment; source preserved'})
        doc.save(target,garbage=4,deflate=True)
        # Compare against the actual baseline at 2x; paper originals remain read-only.
        with f.open(baseline_file) as before:
            for pi in range(len(doc)):
                clip=f.Rect(0,0,source[pi].rect.width,doc[pi].rect.height)
                a=before[pi].get_pixmap(matrix=f.Matrix(2,2),clip=clip,alpha=False).samples
                b=doc[pi].get_pixmap(matrix=f.Matrix(2,2),clip=clip,alpha=False).samples
                report['left_pixel_checks'].append(a==b)
        if not all(report['left_pixel_checks']):raise ValueError('原文区域外观校验失败')
    finally:source.close();doc.close()
    return report
