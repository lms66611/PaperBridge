"""Choose embedded glyph coverage and preserve source scientific scripts."""
import html,pathlib,re,sys

_SUBSCRIPT=str.maketrans('₀₁₂₃₄₅₆₇₈₉₊₋ₐₑₒₓₕₖₗₘₙₚₛₜ','0123456789+-aeoxhklmnpst')
_SUPERSCRIPT=str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿⁱ','0123456789+-ni')
_SCRIPT_CHARS=set('₀₁₂₃₄₅₆₇₈₉₊₋ₐₑₒₓₕₖₗₘₙₚₛₜ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿⁱ')
_TERM=re.compile(r'[A-Za-z0-9₀-₉⁰-⁹ₐₑₒₓₕₖₗₘₙₚₛₜⁿⁱ]+')


def source_script_terms(page,bbox):
    """Read actual source glyph baselines, including ASCII subscripts."""
    import pymupdf as fitz

    box=fitz.Rect(bbox);terms={}
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            line_box=fitz.Rect(line['bbox'])
            if line_box.is_empty or not line_box.intersects(box):continue
            spans=line.get('spans',[])
            if not spans:continue
            weights={}
            for span in spans:
                size=round(span['size'],1)
                weights[size]=weights.get(size,0)+len(span['text'])
            primary=max(weights,key=weights.get) if weights else 0
            baselines=[s['origin'][1] for s in spans if abs(s['size']-primary)<.2
                       for _ in range(max(1,len(s['text'])))]
            if not baselines:continue
            baseline=sorted(baselines)[len(baselines)//2]
            chars=[];styles=[]
            for span in spans:
                shift=span['origin'][1]-baseline
                script='sub' if span['size']<primary*.86 and shift>1 else 'super' if span['size']<primary*.86 and shift< -1 else ''
                for char in span['text']:
                    chars.append(char)
                    styles.append('sub' if char in '₀₁₂₃₄₅₆₇₈₉₊₋ₐₑₒₓₕₖₗₘₙₚₛₜ' else
                                  'super' if char in '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿⁱ' else script)
            spelling=''.join(chars)
            for match in _TERM.finditer(spelling):
                raw=match.group();mark=styles[match.start():match.end()]
                if not any(mark) or not re.search('[A-Za-z]',raw):continue
                normalized=raw.translate(_SUBSCRIPT).translate(_SUPERSCRIPT)
                if any(c in _SCRIPT_CHARS for c in normalized):continue
                signature=(normalized,tuple(mark))
                if normalized in terms and terms[normalized]!=signature:terms[normalized]=None
                elif normalized not in terms:terms[normalized]=signature
    return {key:value[1] for key,value in terms.items() if value is not None}


def scientific_markup(text,font,terms):
    """Apply scripts only where the source has the same complete symbol."""
    if not terms:return paragraph_markup(text,font)
    variants={char:char for char in '0123456789abcdefghijklmnopqrstuvwxyz'}
    for glyph,plain in [('₀₁₂₃₄₅₆₇₈₉ₐₑₒₓₕₖₗₘₙₚₛₜ','0123456789aeoxhklmnpst'),
                        ('⁰¹²³⁴⁵⁶⁷⁸⁹ⁿⁱ','0123456789ni')]:
        for script,base in zip(glyph,plain):variants[base]+=script
    patterns=[]
    for term,styles in sorted(terms.items(),key=lambda item:-len(item[0])):
        expression=''.join('['+re.escape(variants[c])+']' if c in variants else re.escape(c) for c in term)
        patterns.append((re.compile(r'(?<![A-Za-z0-9₀-₉⁰-⁹])'+expression+r'(?![A-Za-z0-9₀-₉⁰-⁹])'),styles))
    hits=[]
    for pattern,styles in patterns:
        for match in pattern.finditer(text):
            if any(match.start()<end and match.end()>start for start,end,_ in hits):continue
            hits.append((match.start(),match.end(),styles))
    parts=[];cursor=0
    for start,end,styles in sorted(hits):
        parts.append(paragraph_markup(text[cursor:start],font))
        token=text[start:end];run='';active=''
        def flush():
            if not run:return ''
            markup=paragraph_markup(run,font)
            return '<'+active+'>'+markup+'</'+active+'>' if active else markup
        for char,style in zip(token,styles):
            if style!=active:parts.append(flush());run='';active=style
            run+=char.translate(_SUBSCRIPT).translate(_SUPERSCRIPT)
        parts.append(flush());cursor=end
    parts.append(paragraph_markup(text[cursor:],font))
    return ''.join(parts)

def paragraph_markup(text,font):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    folders=[pathlib.Path(__file__).resolve().parents[1]/'resources/fonts',
             pathlib.Path(getattr(sys,'_MEIPASS','.'))/'resources/fonts']
    fallbacks=[]
    # Noto Sans Math deliberately omits some common superscript/subscript
    # numerals. Embed the general text font as well; retain Unicode chemistry.
    for name,filename in [('PB-NotoSansMath','NotoSansMath-Regular.ttf'),
                          ('PB-NotoSans','NotoSans-Regular.ttf')]:
        if name not in pdfmetrics.getRegisteredFontNames():
            found=next((p/filename for p in folders if (p/filename).is_file()),None)
            if not found:raise FileNotFoundError(f'缺少随包字体 {filename}，请安装完整更新包。')
            pdfmetrics.registerFont(TTFont(name,str(found)))
        fallbacks.append((name,pdfmetrics.getFont(name).face.charToGlyph))
    cjk=pdfmetrics.getFont(font).face.charToGlyph
    parts=[];active=font;buffer=''
    def flush():
        if buffer:parts.append('<font name="'+active+'">'+html.escape(buffer).replace('\n','<br/>')+'</font>')
    for char in text:
        code=ord(char)
        chosen=font if cjk.get(code) or char in '\n\t\r' else next(
            (name for name,coverage in fallbacks if coverage.get(code)),None)
        if chosen is None:raise ValueError(f'字体缺少 U+{code:04X}；请保留原图或补充字体，未静默输出方框。')
        if chosen!=active:flush();buffer='';active=chosen
        buffer+=char
    flush();return ''.join(parts)
