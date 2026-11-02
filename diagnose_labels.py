"""Read-only label/stage lookup. Never imports an extractor or adds predictions.
Use after prediction to locate a missing pair across raw/filter/main/final layers.
A missing state is NOT automatically a GT error. Source comments are not code.
"""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
from boundary_logic import parse,canonical,positive_states,comparisons,BoundarySyntaxError
from boundary_normalizer import load_graph,empty_graph,normalize_rows,write_csv

LAYERS=[('全量候选','sequence_candidates.csv'),('名称过滤','sequence_candidates_filtered.csv'),
        ('完整候选对','prediction_candidate_pairs.csv'),('完整最终对','final_prediction_pairs.csv'),
        ('工艺精选','final_process_steps.csv')]

def read_rows(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:
        first=f.readline();f.seek(0)
        return list(csv.DictReader(f,delimiter='\t' if '\t' in first else ','))

def diagnose(run,start,end='',graph=None):
    run=Path(run)
    if graph is None:
        gp=run/'source_transition_graph.json';graph=load_graph(gp) if gp.exists() else empty_graph()
    label=positive_states(parse(start))
    if label is None:raise ValueError('诊断入口目前需要单一状态或同变量OR状态起点；不自动修补写法。')
    var,values=label
    obs=[r for r in graph.get('state_observations',[]) if r['variable'].casefold()==var.casefold() and r['value'] in values]
    edges=[r for r in graph.get('transitions',[]) if r['variable'].casefold()==var.casefold() and r['from'] in values]
    comments=[r for r in graph.get('comment_state_diagnostics',[]) if r['variable'].casefold()==var.casefold() and r['comment_number'] in values]
    layers=[];sk=canonical(start);ek=canonical(end) if end else None
    for title,filename in LAYERS:
        p=run/filename
        if not p.exists():layers.append({'阶段':title,'文件':filename,'状态':'未提供/未运行','同起点行数':None,'完整标签对行数':None,'行号':[],'例子':[]});continue
        rows=read_rows(p);starts=[(i+2,r) for i,r in enumerate(rows) if canonical(r.get('开始标签',''))==sk]
        hits=[(n,r) for n,r in starts if ek is None or canonical(r.get('结束标签',''))==ek]
        related=[]
        for i,r in enumerate(rows):
            try:atoms=comparisons(parse(r.get('开始标签','')))
            except BoundarySyntaxError:continue
            if any(a.kind=='eq' and a.args[0].casefold()==var.casefold() and a.args[1] in values for a in atoms):related.append(i+2)
        layers.append({'阶段':title,'文件':filename,'状态':'已读取','同起点行数':len(starts),
                       '完整标签对行数':len(hits) if end else None,'包含该状态的起点行数':len(related),'相关行号':related[:100],'行号':[n for n,r in hits],
                       '例子':[{'行号':n,'候选ID':r.get('候选ID',''),'开始':r.get('开始标签',''),'结束':r.get('结束标签',''),
                                 '证据类型':r.get('状态',r.get('核验状态',''))} for n,r in (hits or starts)[:12]]})
    main=next(r for r in layers if r['阶段']=='完整候选对')
    n=(main['完整标签对行数'] if end else main['同起点行数']) or 0
    if n:status='主候选已存在；不是本轮底层漏提';reason='主候选行号见分层结果。若本地最终表没有，请检查阶段、筛选和源码/版本哈希；本诊断不假定本地LLM行为。'
    elif obs or edges:status='源码有状态证据；检查候选生成或过滤';reason='比较/赋值存在不等于完整过程已匹配；不能拼接不同预测行。'
    else:status='当前源码索引未见该状态；版本/标签待核验';reason='未在已解析的可执行状态比较/赋值中找到；不据此自动判GT错误，更不能由注释补造状态。'
    return {'开始标签':start,'结束标签':end,'诊断':status,'说明':reason,'源码SHA256':graph.get('source_sha256',''),
            '源码名称':graph.get('source_name',''),'源码状态证据':obs,'源码后继':edges,
            '相关注释冲突':comments,'输出阶段':layers,'解析限制':graph.get('limits',graph.get('warnings',[])),
            '行号口径':'输出阶段行号为CSV记录行号（含表头），不是含多行单元格的文本物理行号','不是覆盖率统计':not bool(end)}

def run_lookup(run,starts,out,ends=None):
    out=Path(out);run=Path(run).resolve()
    if out.resolve()==run:raise ValueError('诊断输出需独立子目录；不得覆盖预测文件')
    if ends and len(ends)!=len(starts):raise ValueError('开始/结束标签数量不同')
    out.mkdir(parents=True,exist_ok=True)
    gp=run/'source_transition_graph.json';graph=load_graph(gp) if gp.exists() else empty_graph()
    records=[diagnose(run,s,ends[i] if ends else '',graph) for i,s in enumerate(starts)]
    (out/'label_lookup.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    flat=[]
    for r in records:
        flat.append({k:v for k,v in r.items() if not isinstance(v,(list,dict))}|{
           '源码后继值':';'.join(map(str,sorted({e['to'] for e in r['源码后继']}))),
           '主候选行号':';'.join(map(str,next(x['行号'] for x in r['输出阶段'] if x['阶段']=='完整候选对'))),
           '相关注释冲突数量':len(r['相关注释冲突'])})
    write_csv(out/'label_lookup.csv',flat)
    return records

if __name__=='__main__':
    p=argparse.ArgumentParser(description='只读定位标签在源码/候选/精选哪个阶段缺失')
    p.add_argument('--run',required=True);p.add_argument('--start',action='append',required=True)
    p.add_argument('--end',action='append');p.add_argument('--out',required=True)
    a=p.parse_args();r=run_lookup(a.run,a.start,a.out,a.end)
    for x in r:print(x['开始标签'],x['结束标签'],x['诊断'])
