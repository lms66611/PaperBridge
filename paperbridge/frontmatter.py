"""Recover publication artwork and keyword rows from verified source geometry."""
import copy,re


def compact(text):
    return re.sub(r'\s+','',text).casefold()


def publication_masthead(page,segments):
    import pymupdf as f
    if page.number!=0 or page.rotation:return None
    titles=[s for s in segments if s.get('kind')=='title' and len(s.get('source',''))>40
            and s['bbox'][2]-s['bbox'][0]>page.rect.width*.4]
    if not titles:return None
    title=min(titles,key=lambda s:s['bbox'][1]);top=float(title['bbox'][1])
    # Detector boxes can trim the ascenders of the article title.
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            text=''.join(s['text'] for s in line['spans']).strip()
            if len(text)>20 and compact(title['source']).startswith(compact(text)):
                top=min(top,line['bbox'][1])
    if not 65<top<page.rect.height*.45:return None
    header=page.get_text('text',clip=f.Rect(0,0,page.rect.width,top-4))
    if not re.search(r'journal\s+homepage|contents\s+lists\s+available|www\.|https?://',header,re.I):return None
    rules=[]
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0]=='l':
                a,b=item[1:3]
                if abs(a.y-b.y)<.5 and abs(a.x-b.x)>page.rect.width*.6 and 45<a.y<top-4:rules.append(a.y)
    # A separator avoids copying a partial badge beside the article title.
    if not rules:return None
    return f.Rect(0,0,page.rect.width,max(rules)+1)


def publication_footer(page):
    """Preserve publisher metadata that is intentionally outside text segments."""
    import pymupdf as f
    if page.number != 0 or page.rotation:
        return None
    lines=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            rect=f.Rect(line['bbox'])
            if rect.y0 < page.rect.height*.78:
                continue
            text=''.join(span['text'] for span in line['spans'])
            if text.strip():
                lines.append((rect,text))
    metadata=' '.join(text for _,text in lines)
    if not re.search(r'Corresponding author',metadata,re.I) or not re.search(r'(?:doi\.org|10\.\d{4,9}/)',metadata,re.I):
        return None
    markers=[rect.y0 for rect,text in lines if re.search(r'Corresponding author',text,re.I)]
    if not markers:return None
    top=min(markers)
    bottom=max(rect.y1 for rect,_ in lines if rect.y0>=top-1)
    return f.Rect(0,max(0,top-5),page.rect.width,min(page.rect.height,bottom+4))


def affiliation_rows(page,segment):
    """Recover the source's lettered affiliation baselines and split the translation."""
    import pymupdf as f
    if page.number != 0 or segment.get('kind') != 'plain text':
        return []
    box=f.Rect(segment['bbox'])
    rows=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            rect=f.Rect(line['bbox'])
            if (rect & box).get_area() < rect.get_area()*.5:
                continue
            text=''.join(span['text'] for span in line['spans']).strip()
            match=re.match(r'^([a-z])\s+(.+)',text)
            if match:
                rows.append((match[1],text,rect))
    if len(rows)<2 or [letter for letter,_,_ in rows]!=list('abcdef')[:len(rows)]:
        return []
    translation=segment.get('translation','')
    pieces=[p.strip() for p in re.split(r'[;；]\s*(?=[a-z]\s*)',translation) if p.strip()]
    if len(pieces)!=len(rows) or any(not re.match(r'^'+letter+r'\s+',part) for (letter,_,_),part in zip(rows,pieces)):
        return []
    children=[]
    for i,((letter,text,rect),target) in enumerate(zip(rows,pieces)):
        child=copy.deepcopy(segment)
        next_top=rows[i+1][2].y0 if i+1<len(rows) else rect.y0+(rows[-1][2].y0-rows[-2][2].y0)
        child.update(id=segment['id']+f':aff{letter}',source=text,translation=target,
                     bbox=[rect.x0,rect.y0,page.rect.width-35,next_top-.2],kind='affiliation')
        children.append(child)
    return children


def keyword_rows(page,segment):
    import pymupdf as f
    if not re.match(r'^\s*(?:Keywords?|关键词)\s*[:：]',segment.get('source',''),re.I):return []
    box=f.Rect(segment['bbox']);rows=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            rect=f.Rect(line['bbox'])
            if (rect & box).get_area()<rect.get_area()*.5:continue
            text=''.join(s['text'] for s in line['spans']).strip()
            if text:rows.append({'source':text,'bbox':list(rect)})
    rows.sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
    if len(rows)<3 or not re.fullmatch(r'(?:Keywords?|关键词)\s*[:：]',rows[0]['source'],re.I):return []
    if compact(' '.join(r['source'] for r in rows))!=compact(segment['source']):return []
    return rows


def keyword_translations(rows,text):
    match=re.match(r'^\s*(关键词|关键字|Keywords?)\s*[:：]\s*(.*)$',text,re.I|re.S)
    if not match:return None
    body=match[2].strip();terms=[r['source'] for r in rows[1:]]
    explicit=[p.strip() for p in re.split(r'[;；\n]+',body) if p.strip()]
    if len(explicit)==len(terms):parts=explicit
    else:
        # Protect unchanged multiword terms before reading whitespace list delimiters.
        protected={};remaining=body
        for term in sorted(terms,key=len,reverse=True):
            if len(term.split())<2:continue
            pattern=r'(?<!\w)'+r'\s+'.join(re.escape(w) for w in term.split())+r'(?!\w)'
            matches=list(re.finditer(pattern,remaining,re.I))
            if len(matches)==1:
                key=f'\ue000{len(protected)}\ue001';protected[key]=matches[0][0]
                remaining=re.sub(pattern,lambda m:key,remaining,count=1,flags=re.I)
        parts=remaining.split();merged=[];i=0
        while i<len(parts):
            token=parts[i]
            # An unchanged code belonging to a longer source term, e.g. RP-3 aviation fuel.
            prefix=any(t.casefold().startswith(token.casefold()+' ') for t in terms)
            if prefix and i+1<len(parts) and re.search('[\u3400-\u9fff]',parts[i+1]):
                merged.append(token+' '+parts[i+1]);i+=2
            else:merged.append(token);i+=1
        parts=[protected.get(p,p) for p in merged]
    if len(parts)!=len(terms):return None
    from .alignment import facts
    if any(facts(a)!=facts(b) for a,b in zip(terms,parts)):return None
    if compact(' '.join(parts))!=compact(re.sub(r'[;；\n]+',' ',body)):return None
    return [match[1]+('：' if re.search('[\u3400-\u9fff]',match[1]) else ':')]+parts


def expand_keyword_rows(page,segment,neighbors,translated=False):
    rows=keyword_rows(page,segment)
    if not rows:return [segment]
    targets=keyword_translations(rows,segment.get('translation','')) if translated else None
    if translated and targets is None:return [segment]
    right=page.rect.width-20
    top,bottom=rows[0]['bbox'][1],rows[-1]['bbox'][3]
    for other in neighbors:
        if other is segment:continue
        b=other['bbox']
        if b[0]>segment['bbox'][2]+3 and b[1]<bottom and b[3]>top:right=min(right,b[0]-12)
    # Give complete technical terms the unused width before the abstract column.
    right=max(segment['bbox'][2],right)
    gaps=[b['bbox'][1]-a['bbox'][1] for a,b in zip(rows,rows[1:])]
    if min(gaps)<5:return [segment]
    children=[]
    for i,row in enumerate(rows):
        child=copy.deepcopy(segment);x0,y0,x1,y1=row['bbox']
        end=rows[i+1]['bbox'][1]-.2 if i+1<len(rows) else y0+min(gaps)-.2
        child.update(id=segment['id']+f':k{i}',source=row['source'],
                     bbox=[x0,y0,right,min(y1,end)],kind='keyword-label' if i==0 else 'keyword',
                     type='text',keyword_parent=segment['id'])
        if translated:child['translation']=targets[i]
        children.append(child)
    return children
