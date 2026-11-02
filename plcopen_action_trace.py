"""Prediction-only PLCopen state-seed -> actual wired process command search.
No gold tables are accepted/read. The graph describes electrical/data connectivity,
not runtime reachability or time equivalence.
"""
import csv, json, re
from pathlib import Path
from collections import defaultdict, deque, Counter
from lxml import etree
from plcopen_controlflow import local, cmp_atom, pin, get_value, links, expression, NUMBER
from process_semantics import MECHANISM, PROCESS

FIELDS=['source_unit','body_kind','state_variable','state_value','eq_id','target_id','target_type','target_symbol','category','path_ids','next_state','boundary_condition','evidence_level','caveat']
IGNORE = re.compile(r'^(?:EQ|AND|OR|NOT|MOVE|TON|TOF|TP|R_TRIG|F_TRIG|ADD|SUB|MUL|DIV)$',re.I)
SAFETY = re.compile(r'安全|safety|alarm|报警|故障|error|interlock|使能许可|权限|homed',re.I)

def symbol(node):
    typ=local(node)
    if typ=='block':
        name=node.get('typeName','')
        args=[]
        for sec in node:
            if local(sec)!='inputVariables':continue
            for v in sec:
                if local(v)=='variable' and (v.get('formalParameter','').upper() not in ('EN','ENO')):
                    val=expression(v)
                    if val:args.append(f"{v.get('formalParameter','')}={val}")
        return name, ';'.join(args[:8])
    if typ=='coil':
        var=expression(node)
        if not var:
            for child in node:
                if local(child)=='variable':var=''.join(child.itertext()).strip();break
        return 'coil',var
    return typ,expression(node)

def classify(kind,name,details):
    if kind=='MOVE':return 'transition'
    if kind.upper() in ('TON','TOF','TP'):return 'timer'
    if kind=='coil' or kind=='block':
        if SAFETY.search(name) and not (PROCESS.search(name) and '检测' in name):return 'auxiliary_guard'
        if MECHANISM.search(name) or (kind=='block' and MECHANISM.search(details)):return 'actuator_candidate'
        if PROCESS.search(name) or PROCESS.search(details):return 'process_candidate'
        return 'unclassified_coil' if kind=='coil' else 'auxiliary_block'
    return 'other'

def scan(source, max_depth=28):
    rows=[];stats=Counter()
    for _,unit in etree.iterparse(str(source),events=('end',),tag=('{*}LD','{*}FBD'),huge_tree=True):
        stats['networks']+=1
        owner='unknown';p=unit.getparent()
        while p is not None:
            if local(p) in ('pou','action'):
                owner=p.get('name') or 'unknown';break
            p=p.getparent()
        nodes={n.get('localId'):n for n in unit if n.get('localId')}
        forward=defaultdict(set)
        for dst,node in nodes.items():
            for src in links(node):
                if src in nodes and src!=dst:forward[src].add(dst)
        eqs=[]
        for nid,node in nodes.items():
            if local(node)=='block':
                atom=cmp_atom(node,nodes)
                if atom:eqs.append((nid,atom))
        stats['state_eq_seeds']+=len(eqs)
        for eqid,(variable,value) in eqs:
            queue=deque([(eqid,(eqid,))]);visited={eqid};found=0
            while queue:
                nid,path=queue.popleft()
                if len(path)>max_depth:stats['depth_limited']+=1;continue
                for nextid in sorted(forward.get(nid,())):
                    if nextid in visited:continue
                    visited.add(nextid);node=nodes[nextid];typ=local(node)
                    name,detail=symbol(node)
                    category=classify(typ if typ=='coil' else ('MOVE' if name.upper()=='MOVE' else 'block' if typ=='block' else typ),name,detail)
                    next_state='';boundary=''
                    if typ=='block' and name.upper()=='MOVE':
                        inn=pin(node,'inputVariables','IN');out=pin(node,'outputVariables','OUT')
                        if inn is not None and out is not None:
                            val=get_value(inn,nodes);dest=get_value(out,nodes)
                            if dest==variable and NUMBER.fullmatch(val):next_state=f'{variable}={NUMBER.fullmatch(val).group(1)}'
                    if typ=='contact':boundary=expression(node)
                    if typ in ('coil','block') and category in ('actuator_candidate','process_candidate','timer','transition','unclassified_coil'):
                        found+=1;stats[category]+=1
                        rows.append(dict(source_unit=owner,body_kind=local(unit),state_variable=variable,state_value=value,eq_id=eqid,
                            target_id=nextid,target_type=name,target_symbol=detail,category=category,path_ids='>'.join(path+(nextid,)),
                            next_state=next_state,boundary_condition=boundary,evidence_level='wired_path_observed',
                            caveat='EQ至目标有连接路径；并非完整布尔使能证明，也不保证动作执行、物理完成或计时等价'))
                    # Do not walk backwards into unrelated subgraphs; do not propagate through terminal coils/MOVE.
                    if typ!='coil' and name.upper()!='MOVE':queue.append((nextid,path+(nextid,)))
            if found==0:stats['seeds_without_process_targets']+=1
        unit.clear()
        while unit.getprevious() is not None:del unit.getparent()[0]
    return rows,dict(stats)

def generate(source,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    rows,stats=scan(source)
    with (out/'plcopen_action_trace.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
    (out/'plcopen_action_trace_summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
    return stats
