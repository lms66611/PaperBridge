from __future__ import annotations
import collections, hashlib, json, os, pathlib, re, unicodedata
from .storage import atomic_json

def configure_assets(paths):
    """Redirect the upstream resource root BEFORE importing any BabelDOC modules."""
    os.environ['PAPERBRIDGE_BABELDOC_CACHE']=str(paths.assets/'babeldoc')
    import babeldoc.const as const
    const.CACHE_FOLDER=paths.assets/'babeldoc'
    const.TIKTOKEN_CACHE_FOLDER=const.CACHE_FOLDER/'tiktoken'
    os.environ['TIKTOKEN_CACHE_DIR']=str(const.TIKTOKEN_CACHE_FOLDER)

def language(text):
    han=len(re.findall(r'[\u3400-\u9fff]',text))
    latin=len(re.findall(r'[A-Za-z]',text))
    return 'zh' if (han>0 and latin<3) or (han>=2 and han>latin*.3) else 'en'

def normalize(text):
    # Join typographic line breaks without changing scientific minus signs or numbers.
    text=text.replace('\u00ad','').replace('\x00','')
    text=re.sub(r'([A-Za-z]{3,})-\s*\n\s*([a-z]{2,})',r'\1\2',text)
    text=re.sub(r'(?<=[\u3400-\u9fff])\s*\n\s*(?=[\u3400-\u9fff])','',text)
    text=re.sub(r'\s+',' ',text).strip()
    compact=re.sub(r'[\s■▪●◆•]+','',text).casefold()
    labels={'abstract':'Abstract','articleinfo':'Article information','keywords':'Keywords','references':'References','bibliography':'Bibliography'}
    return labels.get(compact,text)

def reading_order(items,width):
    """Recursive whitespace cuts support asymmetric abstract/keywords and two columns."""
    if len(items)<2:return list(items)
    # Find the column gutter using narrow blocks, independently of full-width
    # titles/figures. A horizontal gap inside the body must never interleave
    # left and right columns.
    narrow=[x for x in items if x['bbox'][2]-x['bbox'][0]<=width*.66]
    intervals=sorted((x['bbox'][0],x['bbox'][2]) for x in narrow)
    cuts=[]
    if intervals:
        high=intervals[0][1]
        for low,end in intervals[1:]:
            cut=(low+high)/2
            if low-high>8 and width*.25<cut<width*.75: cuts.append((low-high,cut))
            high=max(high,end)
    if cuts:
        _,cut=max(cuts)
        spans=sorted([x for x in items if x['bbox'][0]<cut<x['bbox'][2]],key=lambda x:x['bbox'][1])
        remaining=[x for x in items if x not in spans]
        ordered=[]
        def columns(band):
            left=[x for x in band if (x['bbox'][0]+x['bbox'][2])/2<cut]
            right=[x for x in band if x not in left]
            return reading_order(left,width)+reading_order(right,width)
        for span in spans:
            before=[x for x in remaining if (x['bbox'][1]+x['bbox'][3])/2<span['bbox'][1]]
            ordered.extend(columns(before));ordered.append(span)
            remaining=[x for x in remaining if x not in before]
        return ordered+columns(remaining)
    def gaps(axis):
        intervals=sorted((x['bbox'][axis],x['bbox'][axis+2]) for x in items)
        high=intervals[0][1]; result=[]
        for low,end in intervals[1:]:
            if low-high>8:result.append((low-high,(low+high)/2))
            high=max(high,end)
        return result
    horizontal=gaps(1)
    vertical=gaps(0)
    if horizontal:
        _,cut=max(horizontal)
        top=[x for x in items if (x['bbox'][1]+x['bbox'][3])/2<cut]
        bottom=[x for x in items if x not in top]
        if top and bottom:return reading_order(top,width)+reading_order(bottom,width)
    if vertical:
        _,cut=max(vertical)
        left=[x for x in items if (x['bbox'][0]+x['bbox'][2])/2<cut]
        right=[x for x in items if x not in left]
        if left and right:return reading_order(left,width)+reading_order(right,width)
    return sorted(items,key=lambda x:(round(x['bbox'][1]/7),x['bbox'][0]))

class PDFExtractor:
    def __init__(self, paths): self.paths=paths; self.model=None
    def load_model(self):
        configure_assets(self.paths)
        from babeldoc.docvision.doclayout import OnnxModel
        model_path=self.paths.assets/'babeldoc/models/doclayout_yolo_docstructbench_imgsz1024.onnx'
        if not model_path.exists(): raise FileNotFoundError('缺少随包版面模型，请使用完整 ZIP。')
        if not self.model: self.model=OnnxModel(str(model_path))
        return self.model

    def extract(self,pdf_path,job_dir,progress=lambda *x:None):
        import numpy as np
        import pymupdf as fitz
        model=self.load_model()
        assets=job_dir/'figures'; assets.mkdir(exist_ok=True)
        doc=fitz.open(pdf_path)
        if doc.needs_pass: raise ValueError('PDF 有密码保护，请先提供可读取的副本。')
        all_items=[]; diagnostics=[]; source_pages=[]
        full_text='\n'.join(page.get_text() for page in doc)
        if sum(c.isalpha() for c in full_text)<100:
            raise ValueError('此 PDF 缺少可用文字层，首版暂不支持扫描件 OCR。')
        metadata=dict(doc.metadata or {})
        doi_match=re.search(r'10\.\d{4,9}/[^\s<>"\]]+',full_text[:14000],re.I)
        metadata['doi']=doi_match[0].rstrip('.,;)') if doi_match else ''
        reference_mode=False
        for page_index,page in enumerate(doc):
            progress('解析版面',page_index/max(1,len(doc)))
            scale=1.5
            pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
            image=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)[:,:,:3][:,:,::-1]
            result=model.predict(image)[0]
            regions=[]
            for box in result.boxes:
                rect=[float(v)/scale for v in box.xyxy]
                label=result.names[int(box.cls)]
                regions.append({'bbox':rect,'label':label,'score':float(box.conf),'lines':[]})
            # Assign every extracted line once, independently of PDF object order.
            unassigned=[]
            for block in page.get_text('dict')['blocks']:
                if block.get('type')!=0: continue
                for line in block.get('lines',[]):
                    spans=line.get('spans',[])
                    text=''.join(s.get('text','') for s in spans).strip()
                    if not text: continue
                    bbox=list(line['bbox']); r=fitz.Rect(bbox)
                    candidates=[]
                    for i,region in enumerate(regions):
                        overlap=(r & fitz.Rect(region['bbox'])).get_area()/max(1,r.get_area())
                        if overlap>.35: candidates.append((overlap, -fitz.Rect(region['bbox']).get_area(),i))
                    entry={'bbox':bbox,'text':text,'font_size':max((s['size'] for s in spans),default=10)}
                    if candidates: regions[max(candidates)[2]]['lines'].append(entry)
                    else: unassigned.append(entry)
            # Preserve uncovered text as blocks; don't silently discard parser leftovers.
            for line in unassigned:
                if line['bbox'][1]<22 or line['bbox'][3]>page.rect.height-15:
                    diagnostics.append(f'p{page_index+1}: 页边文字已保留在原始 PDF：{line["text"][:70]}')
                    continue
                match=next((r for r in regions if r['label']=='unclassified' and abs(r['bbox'][0]-line['bbox'][0])<8 and 0<=line['bbox'][1]-r['bbox'][3]<12),None)
                if match:
                    match['lines'].append(line); match['bbox'][3]=line['bbox'][3]
                else:
                    regions.append({'bbox':line['bbox'][:],'label':'unclassified','score':0,'lines':[line]})
            page_items=[]
            for n,region in enumerate(regions):
                label=region['label'].lower()
                lines=sorted(region['lines'],key=lambda x:(round(x['bbox'][1]/3),x['bbox'][0]))
                text=normalize('\n'.join(x['text'] for x in lines))
                if label in ['abandon','page_header','page_footer']:
                    if text: diagnostics.append(f'p{page_index+1}: 页眉页脚：{text[:100]}')
                    continue
                rect=fitz.Rect(region['bbox']) & page.rect
                if rect.is_empty: continue
                is_graphic=label in ['figure','table','isolate_formula']
                if is_graphic:
                    image_path=assets/f'p{page_index+1:03d}_{n:03d}.png'
                    page.get_pixmap(matrix=fitz.Matrix(2.1,2.1),clip=rect,alpha=False).save(image_path)
                    page_items.append({'type':'graphic','kind':label,'bbox':list(rect),'page':page_index+1,
                                       'image':str(image_path.relative_to(job_dir)),'source':text,'language':language(text),
                                       'translation':'','status':'pending' if label=='table' and text else 'preserved','issues':[]})
                    continue
                if not text: continue
                # Detector regions may overlap even when their assigned PDF text
                # lines do not. Anchor text to those lines, not the model box.
                if lines:
                    rect=fitz.Rect(lines[0]['bbox'])
                    for line in lines[1:]:rect |= fitz.Rect(line['bbox'])
                item_type='heading' if label in ['title','section_header'] or (len(text)<160 and re.match(r'^\d+(?:\.\d+)*\.?\s+[A-Z\u3400-\u9fff]',text)) else 'text'
                if re.match(r'^(?:references|bibliography|参考文献|參考文獻)\s*$',text,re.I): reference_mode=True
                page_items.append({'type':item_type,'kind':label,'bbox':list(rect),'page':page_index+1,
                                   'source':text,'language':language(text),'translation':'',
                                   'status':'reference' if reference_mode else 'pending','issues':[]})
            ordered=reading_order(page_items,page.rect.width)
            # Reference detection follows reading order, not model detection order.
            all_items.extend(ordered)
            if len(page.get_text().strip())<25: diagnostics.append(f'p{page_index+1}: 本页文字层很少，请核对图像。')
            source_pages.append({'page':page_index+1,'width':page.rect.width,'height':page.rect.height})
        # Drop only short, repeated page-edge running headers; record their removal.
        header_counts=collections.Counter(normalize(s['source']) for s in all_items if s['type']!='graphic' and s['bbox'][1]<60 and len(s['source'])<110)
        repeated={t for t,c in header_counts.items() if c>=max(3,len(doc)//3)}
        all_items=[s for s in all_items if not (s['bbox'][1]<60 and s['source'] in repeated)]
        # Running headers can span the column gutter and obstruct whitespace cuts.
        # Recompute page order after those headers are removed.
        reordered=[]
        for page_index in range(1,len(doc)+1):
            reordered.extend(reading_order([s for s in all_items if s['page']==page_index],source_pages[page_index-1]['width']))
        all_items=reordered
        # Keep keyword items separate before translation; their order and geometry
        # must survive normalization, including multiword technical terms.
        from .frontmatter import expand_keyword_rows
        structured=[]
        for item in all_items:
            item.setdefault('id',f'p{item["page"]:03d}-raw{len(structured)}')
            neighbors=[s for s in all_items if s['page']==item['page']]
            structured.extend(expand_keyword_rows(doc[item['page']-1],item,neighbors))
        all_items=structured
        # Keep each source region anchored to its original page and column.
        # Neighbouring text is supplied as translation context instead of
        # merging across columns/pages and losing the layout correspondence.
        # Apply references section based on final order.
        reference_mode=False
        for i,item in enumerate(all_items):
            item['id']=f'p{item["page"]:03d}-s{i+1:04d}'
            if re.match(r'^(?:references|bibliography|参考文献|參考文獻)\s*$',item['source'],re.I): reference_mode=True
            if reference_mode and item['type']!='graphic': item['status']='reference'
            elif item.get('status')=='reference': item['status']='pending'
            if (item['page']==1 and item['type']=='text' and len(item['source'])<400
                    and (len(re.findall(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+(?=[\d\s,|*§†‡]|$)',item['source']))>=2
                         or ('|' in item['source'] and re.search(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+',item['source'])))
                    and any(c in item['source'] for c in [',','|','*'])
                    and not re.search(r'University|Institute|Department|School|Laboratory',item['source'])):
                item['status']='preserved';item['kind']='author_names'
        titles=[i['source'] for i in all_items if i['kind']=='title' and i['page']==1]
        metadata['title']=next((t for t in titles if len(t)>8 and not re.match(r'^\d+\.?\s',t) and normalize(t) not in ['Abstract','Article information','Keywords']),None)
        if not metadata['title']:
            metadata['title']=next((s['source'] for s in all_items[:5] if s['page']==1 and 40<len(s['source'])<300
                                     and s['type']!='graphic' and not re.match(r'^\d+\.?\s',s['source'])),None)
        metadata['title']=metadata['title'] or metadata.get('title') or pathlib.Path(pdf_path).stem
        if len(metadata['title'])<10: metadata['title']=pathlib.Path(pdf_path).stem
        doc.close()
        parsed={'metadata':metadata,'segments':all_items,'pages':source_pages,'diagnostics':diagnostics}
        atomic_json(job_dir/'extracted.json',parsed)
        return parsed
