"""CSV/TSV/Markdown tables with quoted multiline fields and stable source locations.
No inferred operators, truth correction or project-specific variable names.
"""
from pathlib import Path
import csv,re,html
START=('开始标签','开始','人工开始标签','GT开始')
END=('结束标签','结束','人工结束标签','GT结束')
DESC=('动作描述','人工动作描述','机构动作','动作')

def records(path):
    p=Path(path);txt=p.read_text(encoding='utf-8-sig');lines=txt.splitlines()
    if any(l.lstrip().startswith('|') for l in lines[:10]):
        out=[]
        for i,l in enumerate(lines,1):
            if not l.strip().startswith('|'):continue
            cells=[html.unescape(re.sub(r'<br\s*/?>','\n',x,flags=re.I)).strip() for x in re.split(r'(?<!\\)\|',l.strip().strip('|'))]
            if cells and all(re.fullmatch(r':?-{2,}:?',x.strip()) for x in cells):continue
            out.append((i,cells))
        return out
    sep='\t' if any('\t' in l for l in lines[:20]) else ','
    reader=csv.reader(txt.splitlines(keepends=True),delimiter=sep);out=[];previous=0
    for row in reader:
        lineno=previous+1;previous=reader.line_num
        out.append((lineno,[s.strip().replace('\xa0',' ') for s in row]))
    return out

def truth_records(path):
    items=records(path);mapping=None;out=[]
    for line,cells in items:
        if not any(c.strip() for c in cells):continue
        si=next((i for i,c in enumerate(cells) if c in START),None)
        ei=next((i for i,c in enumerate(cells) if c in END),None)
        if si is not None and ei is not None:
            di=next((i for i,c in enumerate(cells) if c in DESC),mapping[2] if mapping else (0 if si!=0 and ei!=0 else None))
            # Some spreadsheets put a secondary two-column header below a 3-column section title.
            if len(cells)==2 and mapping is not None:continue
            mapping=(si,ei,di);continue
        if cells[0] in ('机构动作','动作描述') and len(cells)>1 and cells[1] in ('A','B',''):continue
        if mapping is None:
            if len(cells)<3:continue
            mapping=(1,2,0)
        si,ei,di=mapping
        start=cells[si] if si<len(cells) else '';end=cells[ei] if ei<len(cells) else ''
        desc=cells[di] if di is not None and di<len(cells) else ''
        if not start and not end:continue
        if start in ('A','B','开始') or end=='结束':continue
        out.append({'开始标签':start,'结束标签':end,'动作描述':desc,'人工源文件行号':line})
    return out

def prediction_records(path):
    items=records(path)
    if not items:return []
    _,header=items[0]
    if '开始标签' not in header or '结束标签' not in header:raise ValueError('预测表必须含 开始标签、结束标签 表头')
    return [{**dict(zip(header,cells)),'预测源文件行号':line} for line,cells in items[1:] if any(cells)]
