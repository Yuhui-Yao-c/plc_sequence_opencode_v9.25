#!/usr/bin/env python3
"""Evaluation-side GT conversion: 采集表 xlsx/xls -> TSV(开始标签/结束标签/动作描述).

This tool is used ONLY by the evaluation workflow. Prediction never reads GT
files or this converter. Layout rules are asserted loudly so silent column
misalignment cannot produce a wrong GT.
"""
import argparse,csv,sys
from pathlib import Path

def cell(ws,i,j):
    v=ws.cell_value(i,j)
    return v if isinstance(v,str) else ('' if v is None else str(v))

def cell_o(ws,i,j):
    """openpyxl cell with 0-based indices (xlrd-style)."""
    v=ws.cell(row=i+1,column=j+1).value
    return v if isinstance(v,str) else ('' if v is None else str(v))

def clean(v):
    return v.replace('\r\n','\n').strip()

def emit(rows,start,end,desc,src_line):
    rows.append({'开始标签':clean(start),'结束标签':clean(end),'动作描述':clean(desc),'人工源文件行号':src_line})

def convert_baomylar(path,rows):
    import openpyxl
    wb=openpyxl.load_workbook(path,data_only=True,read_only=False)
    name=next(n for n in wb.sheetnames if '合芯动作拆解' in n)
    ws=wb[name]
    # Header groups verified on 20260913 file: pair1 J/K (idx 9,10), pair2 L/M (idx 12,13), desc col D (idx 3).
    assert cell_o(ws,1,9).strip()=='开始' and cell_o(ws,1,10).strip()=='结束', (cell_o(ws,1,9),cell_o(ws,1,10))
    assert cell_o(ws,1,12).strip()=='开始' and cell_o(ws,1,13).strip()=='结束', (cell_o(ws,1,12),cell_o(ws,1,13))
    for i in range(2,ws.max_row):
        desc=cell_o(ws,i,3)
        for a,b in ((9,10),(12,13)):
            s,e=cell_o(ws,i,a),cell_o(ws,i,b)
            if clean(s) or clean(e):
                emit(rows,s,e,desc,f'{name}!{i+1}')
    wb.close()

def convert_shijiechai(path,rows,sheet_hint='时序拆解'):
    import xlrd
    wb=xlrd.open_workbook(path)
    name=next(n for n in wb.sheet_names() if sheet_hint in n)
    ws=wb.sheet_by_name(name)
    header=None
    for i in range(min(6,ws.nrows)):
        vals=[cell(ws,i,j).strip() for j in range(ws.ncols)]
        if '开始标签' in vals and '结束标签' in vals:
            header=(i,vals.index('开始标签'),vals.index('结束标签'),
                    vals.index('动作描述') if '动作描述' in vals else None);break
    assert header,'时序拆解 header row with 开始标签/结束标签 not found'
    hr,si,ei,di=header
    for i in range(hr+1,ws.nrows):
        s,e=cell(ws,i,si),cell(ws,i,ei)
        desc=cell(ws,i,di) if di is not None else ''
        if clean(s) or clean(e):
            emit(rows,s,e,desc,f'{name}!{i+1}')

LAYOUTS={'baomylar':('包mylar/步序时长采集-XM2-L5-包mylar-20260913.xlsx',convert_baomylar),
         'chaosheng':('超声/步序时长采集-XM2-L5-超声波-20260910.xls',lambda p,r:convert_shijiechai(p,r)),
         'zhuanjiepian':('转接片/4.厦门2转接片 22PPM转接片焊接机步序采集表-20260905步序拆解(已自动还原).xls',
                         lambda p,r:convert_shijiechai(p,r))}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--project',required=True,choices=sorted(LAYOUTS))
    ap.add_argument('--source-root',default='source')
    ap.add_argument('--out',required=True,help='output TSV path (evaluation area only)')
    a=ap.parse_args()
    rel,fn=LAYOUTS[a.project]
    src=Path(a.source_root)/rel
    if not src.is_file():raise FileNotFoundError(src)
    rows=[];fn(src,rows)
    out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=['开始标签','结束标签','动作描述','人工源文件行号'],delimiter='\t')
        w.writeheader();w.writerows(rows)
    empty=sum(1 for r in rows if not r['开始标签'] or not r['结束标签'])
    print(f'{a.project}: {len(rows)} GT rows ({len(rows)-empty} complete pairs, {empty} single-side) -> {out}')

if __name__=='__main__':
    sys.exit(main())
