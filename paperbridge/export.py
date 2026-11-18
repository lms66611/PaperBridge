from __future__ import annotations
import hashlib, html, io, pathlib, re
from .storage import atomic_json

def xml_safe(text):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]',
                  lambda m:f'⟦U+{ord(m[0]):04X}⟧',str(text))

def bilingual(segment):
    """Legacy text pair helper. PDF export keeps the original on the left."""
    source,target=segment.get('source',''),segment.get('translation','')
    if segment.get('status') in ('reference','preserved'): return source,source
    return (target or '【英文待翻译】',source) if segment.get('language')=='zh' else (source,target or '【中文待翻译】')

def translated_text(segment):
    if segment.get('status') in ('reference','preserved'): return segment.get('source','')
    return segment.get('translation','').strip() or '【本段尚未翻译，请重试】'

def export_document(job,paths):
    """Put each source page and its complete translation on one spread."""
    import pymupdf as fitz
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    from reportlab.pdfgen.canvas import Canvas
    from .extract import reading_order,normalize
    from .typography import paragraph_markup,scientific_markup,source_script_terms
    from .alignment import embed,fingerprint,pdf_rect,line_rects
    from .citations import CitationIndex,destination
    from .frontmatter import publication_masthead,publication_footer,affiliation_rows,expand_keyword_rows

    source_file=paths.jobs/job['id']/'source.pdf'
    cjk=next((paths.assets/'fonts').glob('NotoSansSC*.ttf'),None)
    if not cjk: raise FileNotFoundError('缺少随包中文字体，请关联完整便携包的 assets 目录。')
    font='PB-'+hashlib.sha256(str(cjk.resolve()).encode()).hexdigest()[:12]
    if font not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(TTFont(font,str(cjk)))
    output=pathlib.Path(job['settings'].get('output_dir','results'))
    if not output.is_absolute(): output=paths.root/output
    output.mkdir(parents=True,exist_ok=True)
    stem=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',job['title'])[:90].rstrip('. ') or '文献'
    revision=job.get('revision',1)
    prefix=f'{job["sequence"]:02d}_' if job.get('sequence') else ''
    target=output/f'{prefix}{stem}_双语翻译版_{job["id"][:6]}_v{revision}.pdf'
    temp=target.with_suffix('.writing.pdf')
    report={'layout':'original-side-by-side','revision':revision,
            'source_sha256':hashlib.sha256(source_file.read_bytes()).hexdigest(),
            'placements':[],'continuations':[],'notes':[]}
    source=fitz.open(source_file); result=fitz.open(); outlines=[]
    citations=CitationIndex(source,job.get('segments',[]))
    pending_links=[];primary_pages={};page_sources={}
    script_cache={}
    def paragraph(text,size=9.4,segment=None):
        keyword=(segment or {}).get('kind') in ('keyword','keyword-label')
        style=ParagraphStyle('translation',fontName=font,fontSize=size,leading=size*1.30,
                             wordWrap='CJK',splitLongWords=True)
        if keyword:style.leading=size*1.12
        key=(segment['page'],tuple(segment['bbox'])) if segment else None
        if key is not None and key not in script_cache:
            script_cache[key]=source_script_terms(source[int(segment['page'])-1],segment['bbox'])
        terms=script_cache.get(key,{})
        render=lambda part,face:scientific_markup(part,face,terms)
        if (segment or {}).get('kind')=='affiliation' and re.match(r'^[a-z]\s+',text):
            markup='<super>'+paragraph_markup(text[0],font)+'</super>'+render(text[1:],font)
        else:
            markup=citations.markup(xml_safe(text),segment,font,render)
        return Paragraph(markup,style)
    def sheet(w,h):
        buf=io.BytesIO()
        return buf,Canvas(buf,pagesize=(w,h),pageCompression=1)
    def place(canvas,para,box,h):
        para.wrap(box.width,box.height)
        para.drawOn(canvas,box.x0,h-box.y0-para.height)

    alignment={'version':1,'source_fingerprint':fingerprint(source),'pairs':[],'citations':[]}
    anchors={}
    def record_anchor(segment,box,page_index):
        original_page=source[int(segment['page'])-1]
        orig_rect=fitz.Rect(segment['bbox'])
        rects=line_rects(original_page,orig_rect) or [list(orig_rect)]
        item=anchors.get(segment['id'])
        if item is None:
            item={'id':segment['id'],'text':segment.get('source',''),'translation':segment.get('_render_text',translated_text(segment)),
                  'original':[{'page':int(segment['page'])-1,'rects':[pdf_rect(r,original_page.rect.height) for r in rects]}],
                  'source':[],'target':[]}
            anchors[segment['id']]=item;alignment['pairs'].append(item)
        target_page=result[page_index];h=target_page.rect.height
        width=original_page.rect.width;dx=width+18;dy=22
        if not any(a['page']==page_index for a in item['source']):
            item['source'].append({'page':page_index,'rects':[pdf_rect([r[0],r[1]+dy,r[2],r[3]+dy],h) for r in rects]})
        translated_box=fitz.Rect(box)+(dx,dy,dx,dy)
        translated_lines=line_rects(target_page,translated_box) or [list(translated_box)]
        item['target'].append({'page':page_index,'rects':[pdf_rect(r,h) for r in translated_lines]})
    try:
        by_page={p:[] for p in range(1,len(source)+1)}
        for segment in job.get('segments',[]):
            p=int(segment['page'])
            if p not in by_page: raise ValueError(f'段落 {segment["id"]} 页码无效，请重新解析。')
            by_page[p].append(dict(segment))
            # Legacy merged paragraphs are printed once, with explicit pointers
            # at their other source locations. New extraction avoids such merges.
            ranges=segment.get('source_ranges',[])
            if len(ranges)>1:
                report['notes'].append(f'{segment["id"]} 为旧版合并段，完整译文在起始页，其他位置显示指引。')
                for index,location in enumerate(ranges[1:],1):
                    other=int(location['page'])
                    if other not in by_page: raise ValueError('旧版合并段页码无效，请重新解析。')
                    by_page[other].append({**segment,'page':other,'bbox':location['bbox'],
                        'id':segment['id']+f'-续指引{index}','source_ranges':[],
                        'translation':f'【此处接续 {segment["id"]}；完整译文见原文第 {p} 页对应译页。】',
                        'status':'done','type':'text'})

        def repair_overlapping_boxes(page,items):
            """Recover line geometry in jobs extracted with overlapping model boxes."""
            candidates=set()
            for i,left in enumerate(items):
                if left['type']=='graphic' or left.get('status')=='reference':continue
                a=fitz.Rect(left['bbox'])
                for j in range(i+1,len(items)):
                    right=items[j]
                    if right['type']=='graphic' or right.get('status')=='reference':continue
                    b=fitz.Rect(right['bbox'])
                    if (a & b).get_area()>min(a.get_area(),b.get_area())*.75:
                        candidates.update((i,j))
            if not candidates:return
            lines=[(fitz.Rect(line['bbox']),''.join(span['text'] for span in line['spans']))
                   for block in page.get_text('dict')['blocks'] for line in block.get('lines',[])]
            for i in candidates:
                item=items[i];region=fitz.Rect(item['bbox']);target=normalize(item.get('source',''))
                nearby=sorted(((r,t) for r,t in lines if (r & region).get_area()>r.get_area()*.5),
                              key=lambda entry:(entry[0].y0,entry[0].x0))
                matched=None
                for start in range(len(nearby)):
                    for end in range(start+1,len(nearby)+1):
                        sample=normalize('\n'.join(text for _,text in nearby[start:end]))
                        if sample==target:
                            matched=nearby[start:end];break
                        if len(sample)>len(target)+10:break
                    if matched:break
                if not matched:continue
                box=fitz.Rect(matched[0][0])
                for line,_ in matched[1:]:box |= line
                if box.get_area()<region.get_area()*.9:
                    item['bbox']=list(box)
                    report['notes'].append(f'{item["id"]} 已按原 PDF 文字行修正重叠坐标。')

        def compose(index,right_bytes,graphics=()):
            original=source[index];w,h=original.rect.width,original.rect.height
            gutter,header=18,22
            spread=result.new_page(width=2*w+gutter,height=h+header)
            output_index=len(result)-1;page_sources[output_index]=index
            primary_pages[index]=output_index
            for link in citations.links[index]:
                pending_links.append((output_index,dict(link),0,header,'source'))
            left=fitz.Rect(0,header,w,h+header)
            if original.rotation or original.first_annot is not None or original.first_widget is not None:
                # Keep the native text layer selectable underneath the rendered
                # annotation appearance. A screenshot alone breaks correspondence.
                spread.show_pdf_page(left,source,index)
                spread.insert_image(left,pixmap=original.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False,annots=True))
                report['notes'].append(f'p{index+1} 含旋转或批注，左侧以完整渲染保留其外观。')
            else: spread.show_pdf_page(left,source,index)
            with fitz.open(stream=right_bytes,filetype='pdf') as right:
                spread.show_pdf_page(fitz.Rect(w+gutter,header,2*w+gutter,h+header),right,0)
                for link in right[0].get_links():
                    if link.get('uri','').startswith('paperbridge-cite:'):
                        pending_links.append((output_index,link,w+gutter,header,'citation'))
            for graphic in graphics:
                rect=fitz.Rect(graphic['bbox']) & original.rect
                if rect.is_empty: continue
                dest=rect+(w+gutter,header,w+gutter,header)
                if graphic.get('kind') in ('reference','publication-masthead','publication-footer','author-names','formula-label') and not original.rotation:
                    # Use the exact same page transform as the original and
                    # clip its content stream. A separately scaled crop can
                    # change glyph positions by a fraction of a point.
                    spread.show_pdf_page(fitz.Rect(w+gutter,header,2*w+gutter,h+header),source,index)
                    content=spread.get_contents()[-1]
                    clip=f'{dest.x0:.8f} {spread.rect.height-dest.y1:.8f} {dest.width:.8f} {dest.height:.8f} re W n\n'.encode('ascii')
                    result.update_stream(content,b'q\n'+clip+result.xref_stream(content)+b'\nQ\n')
                elif original.rotation:
                    spread.insert_image(dest,pixmap=original.get_pixmap(clip=rect,matrix=fitz.Matrix(2,2),alpha=False))
                else: spread.show_pdf_page(dest,source,index,clip=rect)
                for link in citations.links[index]:
                    clipped=fitz.Rect(link['from']) & rect
                    if not clipped.is_empty:
                        pending_links.append((output_index,{**link,'from':clipped},w+gutter,header,'preserved'))
            buf,labels=sheet(2*w+gutter,header)
            labels.setFont(font,8)
            labels.drawString(10,7,f'原文 p.{index+1} · 原页保留')
            labels.drawString(w+gutter+10,7,f'双语翻译版 · 对应原文 p.{index+1}')
            labels.save()
            with fitz.open(stream=buf.getvalue(),filetype='pdf') as labels_pdf:
                spread.show_pdf_page(fitz.Rect(0,0,2*w+gutter,header),labels_pdf,0)
            outlines.append([1,f'原文第 {index+1} 页',len(result)])

        for index,original in enumerate(source):
            w,h=original.rect.width,original.rect.height
            segments=by_page[index+1]
            repair_overlapping_boxes(original,segments)
            if original.rotation:
                for s in segments: s['bbox']=list(fitz.Rect(s['bbox'])*original.rotation_matrix)
            segments=reading_order(segments,w)
            buf,canvas=sheet(w,h);graphics=[];occupied=[];drawn=[]
            masthead=publication_masthead(original,segments)
            if masthead is not None:
                graphics.append({'bbox':list(masthead),'kind':'publication-masthead'})
                report.setdefault('preserved_frontmatter',[]).append({'page':index+1,'bbox':list(masthead)})
                segments=[s for s in segments if not masthead.contains(fitz.Rect(s['bbox']))]
            footer=publication_footer(original)
            if footer is not None:
                graphics.append({'bbox':list(footer),'kind':'publication-footer'})
                report.setdefault('preserved_frontmatter',[]).append({'page':index+1,'bbox':list(footer)})
            author=[s for s in segments if s.get('kind')=='author_names']
            for s in author:
                graphics.append({'bbox':list(fitz.Rect(s['bbox'])+(-2,-4,2,2)),'kind':'author-names'})
                report['placements'].append({'id':s['id'],'page':index+1,'where':'original-vector'})
            segments=[s for s in segments if s.get('kind')!='author_names']
            recovered=[]
            for s in segments:
                children=affiliation_rows(original,s) or expand_keyword_rows(original,s,segments,translated=True)
                if len(children)>1:
                    if children[0].get('kind')=='affiliation':
                        report.setdefault('affiliation_rows',[]).append({'id':s['id'],'page':index+1,'items':len(children)})
                    else:
                        report.setdefault('keyword_lists',[]).append({'id':s['id'],'page':index+1,'items':len(children)-1})
                recovered.extend(children)
            segments=reading_order(recovered,w)
            reference_boxes=citations.preserved[index]
            graphics.extend({'bbox':list(r),'kind':'reference'} for r in reference_boxes)
            graphic_boxes=[fitz.Rect(s['bbox']) for s in segments if s['type']=='graphic']+reference_boxes
            if masthead is not None:graphic_boxes.append(masthead)
            if footer is not None:graphic_boxes.append(footer)
            if not segments:
                place(canvas,paragraph('【本页没有可用译段，请检查文字层或重新解析。】'),fitz.Rect(25,30,w-25,h-30),h)
                report['notes'].append(f'p{index+1} 没有译段，右页已标记。')
            for s in segments:
                if s.get('status')=='reference':
                    report['placements'].append({'id':s['id'],'page':index+1,'where':'original-reference'})
                    # Record a selectable original-to-original reference pair.
                    drawn.append((s,s['bbox']))
                    continue
                s['_render_text']=citations.restore(s,translated_text(s))
                rect=fitz.Rect(s['bbox']) & original.rect
                if rect.is_empty:raise ValueError(f'{s["id"]} 的原文位置无效，无法保持同页排版。')
                if s['type']=='graphic':
                    graphics.append(s)
                    report['placements'].append({'id':s['id'],'page':index+1,'where':'original-graphic'})
                    continue
                formula_regions=[g for g in graphic_boxes if
                                 (g & rect).get_area()>g.get_area()*.8]
                if (s.get('kind')=='unclassified' and len(formula_regions)>=2 and
                        not re.search(r'[A-Za-z]{3,}',s.get('source',''))):
                    graphics.append({'bbox':list(rect),'kind':'formula'})
                    report['placements'].append({'id':s['id'],'page':index+1,'where':'original-formula'})
                    continue
                if s.get('kind')=='formula_caption' and re.fullmatch(r'\s*\(?\d+\)?\s*',s.get('source','')):
                    graphics.append({'bbox':list(rect+(-1,-1,1,1)),'kind':'formula-label'})
                    report['placements'].append({'id':s['id'],'page':index+1,'where':'original-vector'})
                    continue
                lower=[other['bbox'][1] for other in segments if other is not s
                       and other['bbox'][1]>rect.y0+1
                       and min(rect.x1,other['bbox'][2])-max(rect.x0,other['bbox'][0])>3]
                lower.extend(g.y0 for g in graphic_boxes if g.y0>=rect.y1-0.5 and
                             min(rect.x1,g.x1)-max(rect.x0,g.x0)>3)
                bottom=min(lower,default=h-18)-3
                top=max([rect.y0]+[other.y1+2 for other in occupied if
                        min(rect.x1,other.x1)-max(rect.x0,other.x0)>3 and other.y0<rect.y0+1])
                box=fitz.Rect(rect.x0,top,rect.x1,max(top+1,bottom)) & original.rect
                if s.get('kind')=='table_caption' and box.width<170:
                    box.x1=min(w-35,max(box.x1,rect.x0+250))
                keyword=s.get('kind') in ('keyword','keyword-label')
                if keyword:box=rect
                text=s['_render_text'];fitted=None
                collision=any((box & other).width>1 and (box & other).height>1
                              for other in graphic_boxes)
                sizes=(6.8,6.4,6.0,5.6) if s.get('kind')=='affiliation' else (7.0,6.8,6.4) if keyword else (12.5,10.5,9.4,8.5,7.5,7.0) if s['type']=='heading' else (9.4,8.5,7.5,7.0,6.5,6.0) if s.get('kind') in ('figure_caption','table_caption') else (10.5,9.4,8.5,7.5,7.0)
                for size in (() if collision else sizes):
                    candidate=paragraph(text,size,s)
                    if candidate.wrap(box.width,box.height)[1]<=box.height+0.01:
                        fitted=candidate;break
                if fitted is None and not collision and not keyword:
                    graphic_limit=min((g.y0-3 for g in graphic_boxes if g.y0>top+1 and
                                       min(box.x1,g.x1)-max(box.x0,g.x0)>3),default=h-18)
                    for size in sizes:
                        candidate=paragraph(text,size,s)
                        if candidate.wrap(box.width,max(1,graphic_limit-top))[1]<=graphic_limit-top+0.01:
                            fitted=candidate;break
                if fitted is None:
                    raise ValueError(f'{s["id"]} 译文无法在原文第 {index+1} 页完整排版，请调整该段译文或版面。')
                else:
                    place(canvas,fitted,box,h)
                    drawn.append((s,[box.x0,box.y0,box.x1,box.y0+fitted.height]))
                    occupied.append(fitz.Rect(box.x0,box.y0,box.x1,box.y0+fitted.height))
                    if top>rect.y0+1 or fitted.height>box.height+1:
                        report['notes'].append(f'{s["id"]} 译文已在原页空白内顺延排版。')
                    report['placements'].append({'id':s['id'],'page':index+1,'where':'same-position',
                                                 'font_size':fitted.style.fontSize,
                                                 'bbox':[box.x0,box.y0,box.x1,box.y0+fitted.height]})
            # Reference-only pages draw entirely from the source PDF. Force a
            # blank overlay page because ReportLab omits untouched canvases.
            canvas.showPage();canvas.save();compose(index,buf.getvalue(),graphics=graphics)
            for seg,box in drawn:record_anchor(seg,box,len(result)-1)
        if not len(result): raise ValueError('原 PDF 没有页面。')
        link_counts={'source':0,'preserved':0,'citation':0,'unresolved':0}
        unresolved=[]
        numeric_boxes={}
        def numeric_citation_rect(page_index,link_rect):
            if page_index not in numeric_boxes:
                matches=[]
                for block in result[page_index].get_text('rawdict')['blocks']:
                    for line in block.get('lines',[]):
                        chars=[char for span in line.get('spans',[]) for char in span.get('chars',[])]
                        spelling=''.join(char['c'] for char in chars)
                        for match in re.finditer(r'\[\s*\d+(?:\s*[,;–—−-]\s*\d+)*\s*\]',spelling):
                            glyphs=[fitz.Rect(char['bbox']) for char in chars[match.start():match.end()]]
                            if glyphs:
                                box=glyphs[0]
                                for glyph in glyphs[1:]:box|=glyph
                                matches.append(box)
                numeric_boxes[page_index]=matches
            candidates=[box for box in numeric_boxes[page_index] if
                        abs(box.x0+box.x1-link_rect.x0-link_rect.x1)<max(12,link_rect.width+6) and
                        abs(box.y0+box.y1-link_rect.y0-link_rect.y1)<14]
            if not candidates:return link_rect
            closest=min(candidates,key=lambda box:abs(box.x0+box.x1-link_rect.x0-link_rect.x1)+
                        abs(box.y0+box.y1-link_rect.y0-link_rect.y1))
            return closest+(-.4,-.4,.4,.4)
        for output_index,link,dx,dy,side in pending_links:
            rect=fitz.Rect(link['from'])+(dx,dy,dx,dy)
            ref=None
            if side=='citation':
                ref=citations.references.get(link['uri'].split(':',1)[1])
                resolved=(ref['page'],ref['point']) if ref else None
            else:
                resolved=destination(source,link)
                if resolved:
                    ref=next((e for e in citations.references.values() if e['page']==resolved[0] and
                              abs(e['point'].x-resolved[1].x)<4 and abs(e['point'].y-resolved[1].y)<4),None)
            if resolved:
                pg,point=resolved;target_index=primary_pages[pg]
                right=side!='source' and any(b.contains(point) or b.contains(point+(0,3)) for b in citations.preserved[pg])
                target_point=point+((source[pg].rect.width+18 if right else 0),22)
                if ref and side!='source':
                    rect=numeric_citation_rect(output_index,rect)
                annotation={'kind':fitz.LINK_GOTO,'from':rect,'page':target_index,'to':target_point,'zoom':0}
                result[output_index].insert_link(annotation)
                if ref:
                    height=result[output_index].rect.height
                    alignment['citations'].append({'page':output_index,'rect':pdf_rect(rect,height),
                        'reference':ref['text'],'destination':{'page':target_index,
                        'point':[target_point.x,result[target_index].rect.height-target_point.y]}})
            elif link.get('kind') in (fitz.LINK_URI,fitz.LINK_LAUNCH,fitz.LINK_GOTOR):
                annotation={k:v for k,v in link.items() if k not in ('xref','id','nameddest')}
                annotation['from']=rect;result[output_index].insert_link(annotation)
            else:
                link_counts['unresolved']+=1
                unresolved.append({'source_page':page_sources[output_index]+1,'name':link.get('nameddest'),
                                   'reason':'原 PDF 未提供可解析的目标，未猜测跳转位置。'})
                continue
            link_counts[side]+=1
        report['links']=link_counts
        report['unresolved_source_links']=unresolved
        report['references']={'regions_preserved':sum(map(len,citations.preserved.values())),
                              'citation_spellings_restored':citations.restored,
                              'unmatched_citations':citations.unmatched}
        result.set_toc(outlines)
        embed(result,alignment)
        result.set_metadata({'title':job['title']+' · 双语翻译版','author':'PaperBridge',
                             'subject':'左侧保留原 PDF 页面；右侧对应同页译文。'})
        result.save(temp,garbage=4,deflate=True);temp.replace(target)
    finally:
        result.close();source.close()
        if temp.exists(): temp.unlink()
    report['pdf']=str(target);report['flags']=job.get('review_flags',[])
    atomic_json(paths.jobs/job['id']/f'layout-report-v{revision}.json',report)
    return {'pdf':str(target)}
