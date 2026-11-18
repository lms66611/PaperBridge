"""Keep bibliography artwork and build verifiable PDF citation destinations.

PDF named destinations use PDF coordinates; get_links() exposes them differently
from direct GoTo links. Resolve them before moving pages into bilingual spreads.
No guessed bibliography entry or unverified author/year match becomes a link.
"""
from __future__ import annotations
import collections, html, re

# Protect citation spelling before either a free service or a local model sees
# it. The renderer additionally resolves citations against the source PDF.
CITATION_PATTERN=re.compile(
    r'\b(?:[A-Z][A-Za-zÀ-ž\x27’-]*(?:\s+[A-Z][A-Za-zÀ-ž\x27’-]*)?|van\s+[A-Z][A-Za-z-]+)'
    r'(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][A-Za-z-]+)?'
    r'\s*(?:,\s*|\(\s*)(?:18|19|20)\d{2}[a-z]?(?!\w)'
    r'|\[\s*\d+(?:\s*[,;–—−-]\s*\d+)*\s*\]')


def destination(doc, link):
    import pymupdf as f
    page=link.get('page',-1)
    if not isinstance(page,int) or not 0<=page<len(doc): return None
    point=f.Point(link.get('to', (0,0)))
    if link.get('nameddest'):
        point=point*doc[page].transformation_matrix
    return page,point*doc[page].rotation_matrix


def author_pattern(author,year):
    name=r'\s*'.join(re.escape(word) for word in author.split())
    return re.compile(r'(?<![\w])'+name+
        r'(?:\s*et\s+al\.?|\s*等(?:人)?|\s*(?:and|&)\s+[A-Z][\w-]+)?'
        r'\s*[,，(（]?\s*'+re.escape(year)+r'(?![\da-z])',re.I)


class CitationIndex:
    def __init__(self,doc,segments):
        import pymupdf as f
        self.doc=doc;self.references={};self.patterns={};self.preserved=collections.defaultdict(list)
        self.links={i:pg.get_links() for i,pg in enumerate(doc)}
        for s in segments:
            if s.get('status')!='reference': continue
            pg=int(s['page'])-1
            # Include glyph extents as well as detector boxes: italic ascenders
            # and hanging indents must not be clipped by layout-model margins.
            box=f.Rect(s['bbox']);glyphs=[]
            for block in doc[pg].get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    r=f.Rect(line['bbox'])
                    if box.contains((r.tl+r.br)/2): glyphs.append(r)
            for r in glyphs:box|=r
            box=(box+(-1,-1,1,1)) & doc[pg].rect
            self.preserved[pg].append(box)
        # Only destinations inside an actual reference region are bibliography
        # entries. Figure/footnote destinations are intentionally not reclassified.
        names=doc.resolve_names()
        entries=[]
        destinations=list(names.items())
        for pg,links in self.links.items():
            for link in links:
                if link.get('kind')==f.LINK_GOTO and not link.get('nameddest'):
                    destinations.append((f'direct-{pg}-{link.get("xref",0)}',link))
        for name,d in destinations:
            # Direct GoTo points have already been transformed by MuPDF.
            if name.startswith('direct-'):resolved=destination(doc,d)
            else:resolved=destination(doc,{**d,'nameddest':name})
            if not resolved:continue
            pg,point=resolved
            boxes=[b for b in self.preserved[pg] if
                   b.x0-3<=point.x<=b.x1+3 and b.y0-6<=point.y<=b.y1+6]
            if not boxes:continue
            box=max(boxes,key=lambda b:b.get_area())
            entries.append({'id':name,'page':pg,'point':point,'region':box})
        for entry in entries:
            pg=entry['page'];point=entry['point'];box=entry['region']
            next_y=min((e['point'].y for e in entries if e['page']==pg and
                        abs(e['point'].x-point.x)<12 and e['point'].y>point.y+2),default=box.y1+2)
            lines=[]
            for block in doc[pg].get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    r=f.Rect(line['bbox']);cx,cy=(r.x0+r.x1)/2,(r.y0+r.y1)/2
                    if box.x0<=cx<=box.x1 and point.y-1<=cy<next_y-1:
                        lines.append((r.y0,r.x0,''.join(s['text'] for s in line['spans'])))
            text='\n'.join(t for _,__,t in sorted(lines)).strip()
            match=re.match(r'^(.+?),.*?\b((?:18|19|20)\d{2}[a-z]?)\b',text,re.S)
            if not text:continue
            entry['text']=text
            self.references[entry['id']]=entry
            if match:
                entry['author'],entry['year']=match.groups()
                self.patterns[entry['id']]=author_pattern(*match.groups())
        self.unmatched=[];self.restored=[]

    def spans(self,segment,text):
        """Return non-overlapping exact author/year or original numeric anchors."""
        source=segment.get('source','');found=[]
        for ident,pattern in self.patterns.items():
            if not pattern.search(source):continue
            for m in pattern.finditer(text):found.append((m.start(),m.end(),ident))
        # Numeric citations (including superscript labels and ranges) are accepted
        # only when the source PDF supplies a destination at that source region.
        import pymupdf as f
        pg=int(segment['page'])-1;box=f.Rect(segment['bbox'])
        for link in self.links.get(pg,[]):
            dest=destination(self.doc,link)
            if not dest or not box.intersects(link['from']):continue
            rect=f.Rect(link['from']);words=[]
            for w in self.doc[pg].get_text('words'):
                wr=f.Rect(w[:4]);intersection=wr & rect
                if not intersection.is_empty and intersection.get_area()/max(1,wr.get_area())>.45:
                    words.append(w[4])
            label=' '.join(words).strip()
            if not re.fullmatch(r'[\[（(]?\d+(?:\s*[,，;–—−-]\s*\d+)*[\])）]?',label):continue
            ident='numeric-'+str(pg)+'-'+str(link.get('xref',0))
            if ident not in self.references:
                target,point=dest
                nearest=[e for e in self.references.values() if e['page']==target and
                         abs(e['point'].x-point.x)<15 and abs(e['point'].y-point.y)<8]
                if not nearest:continue
                self.references[ident]={**nearest[0],'id':ident}
            pattern=re.compile(r'(?<!\d)'+re.escape(label).replace(r'\ ',r'\s*')+r'(?!\d)')
            for m in pattern.finditer(text):found.append((m.start(),m.end(),ident))
        accepted=[]
        for span in sorted(set(found),key=lambda s:(s[0],-(s[1]-s[0]))):
            if not accepted or span[0]>=accepted[-1][1]:accepted.append(span)
        return accepted

    def restore(self,segment,text):
        """Keep each citation's literal source author/year spelling in the target."""
        source=segment.get('source','');edits=[]
        for ident,pattern in self.patterns.items():
            originals=list(pattern.finditer(source));translated=list(pattern.finditer(text))
            if len(originals)!=len(translated):continue
            for a,b in zip(originals,translated):
                literal=a.group()
                # Restore a matching close parenthesis when the source used one.
                end=b.end()
                if a.end()<len(source) and source[a.end()] in ')）' and end<len(text) and text[end] in ')）':
                    literal+=source[a.end()];end+=1
                if literal!=text[b.start():end]:edits.append((b.start(),end,literal,ident))
        last=len(text)+1
        for start,end,literal,ident in sorted(edits,reverse=True):
            if end>last:continue
            text=text[:start]+literal+text[end:];last=start
            self.restored.append({'segment':segment['id'],'reference':ident})
        # Older cached translations may have translated a surname into Chinese.
        # Restore only a uniquely unmatched source citation/year to a uniquely
        # unmatched explicit "…等，year" citation. Ambiguity remains visible.
        missing=collections.defaultdict(list)
        for ident,pattern in self.patterns.items():
            original=list(pattern.finditer(source));translated=list(pattern.finditer(text))
            if len(original)==1 and not translated:
                missing[self.references[ident]['year']].append((ident,original[0].group()))
        for year,candidates in missing.items():
            if len(candidates)!=1:continue
            pattern=re.compile(r'[\u3400-\u9fff]{1,6}等(?:人)?\s*[,，(（]?\s*'+re.escape(year)+r'(?![\da-z])')
            matches=list(pattern.finditer(text))
            if len(matches)!=1:continue
            ident,literal=candidates[0];m=matches[0]
            text=text[:m.start()]+literal+text[m.end():]
            self.restored.append({'segment':segment['id'],'reference':ident})
        for ident,pattern in self.patterns.items():
            original=list(pattern.finditer(source));translated=list(pattern.finditer(text))
            if original and len(original)!=len(translated):
                self.unmatched.append({'segment':segment['id'],'reference':ident,
                                       'source_count':len(original),'target_count':len(translated)})
        return text

    def markup(self,text,segment,font,plain_markup):
        spans=self.spans(segment,text) if segment else []
        parts=[];cursor=0
        for start,end,ident in spans:
            parts.append(plain_markup(text[cursor:start],font))
            parts.append('<link href="paperbridge-cite:'+html.escape(ident,quote=True)+
                         '" color="#0066a3">'+plain_markup(text[start:end],font)+'</link>')
            cursor=end
        parts.append(plain_markup(text[cursor:],font))
        return ''.join(parts)
