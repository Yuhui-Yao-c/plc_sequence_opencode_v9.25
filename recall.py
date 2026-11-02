#!/usr/bin/env python3
"""High-recall *supplementary* static candidates from PLCopen XML.
Never conflates numerical adjacency with confirmed control flow.
Uses source evidence, not project-specific identifiers or answer rows.
"""
import argparse,csv,json,re,collections
from pathlib import Path
from lxml import etree
SYM=r'[A-Za-z_\u0080-\uffff][\w.\[\],]*'
RE_ASSIGN=re.compile(r'('+SYM+r')\s*:=\s*(-?\d+)\s*;',re.I)
RE_COMPARE=re.compile(r'('+SYM+r')\s*(?:=|==)\s*(-?\d+)|(-?\d+)\s*(?:=|==)\s*('+SYM+r')',re.I)
RE_EXPRESSION=re.compile(r'<(?:\w+:)?expression[^>]*>([^<>]{1,400})</(?:\w+:)?expression>')
RE_SIMPLE=re.compile(r'^'+SYM+r'$')
RE_INT=re.compile(r'^-?\d+$')

def local(e):return etree.QName(e).localname

def extract(src):
    parser=etree.XMLParser(resolve_entities=False,no_network=True,huge_tree=True)
    root=etree.parse(str(src),parser).getroot()
    observations=collections.defaultdict(lambda:{'values':set(),'sources':set(),'assigns':set(),'compares':set()})
    transitions=set(); unit_count=0
    # Do not recurse through the graph: each LD instance is processed once.
    for unit in root.iter():
        if local(unit) not in ('LD','FBD','ST'):continue
        unit_count+=1
        par=unit.getparent(); owner='unknown'
        while par is not None:
            if local(par) in ('action','pou'):
                owner=par.get('name') or 'unknown';break
            par=par.getparent()
        if local(unit)=='ST':
            st=''.join(unit.itertext())
            for m in RE_ASSIGN.finditer(st):
                v,n=m.groups(); d=observations[v];d['values'].add(int(n));d['assigns'].add(int(n));d['sources'].add(owner)
            for m in RE_COMPARE.finditer(st):
                v,n=(m.group(1),m.group(2)) if m.group(1) else (m.group(4),m.group(3))
                observations[v]['values'].add(int(n));observations[v]['compares'].add(int(n));observations[v]['sources'].add(owner)
            continue
        nodes={e.get('localId'):e for e in unit if e.get('localId')}
        for block in unit:
            if local(block)!='block' or block.get('typeName','').upper()!='MOVE':continue
            inp=[];out=[]
            for group in block:
                k=local(group)
                if k not in ('inputVariables','outputVariables'):continue
                for var in group:
                    if local(var)!='variable':continue
                    ex=[(''.join(x.itertext())).strip() for x in var.iter() if local(x)=='expression']
                    for conn in var.iter():
                        if local(conn)=='connection':
                            ref=nodes.get(conn.get('refLocalId'))
                            if ref is not None:
                                ex.extend((''.join(x.itertext())).strip() for x in ref.iter() if local(x)=='expression')
                    if k=='inputVariables' and var.get('formalParameter')!='EN':inp.extend(ex)
                    if k=='outputVariables':out.extend(ex)
            for v in out:
                if not RE_SIMPLE.fullmatch(v):continue
                for n in inp:
                    if RE_INT.fullmatch(n):
                        d=observations[v];d['values'].add(int(n));d['assigns'].add(int(n));d['sources'].add(owner)
        # Interpret only directly present comparison blocks; no assumptions about their control-path reachability.
        for block in unit:
            if local(block)!='block' or block.get('typeName','').upper() not in ('EQ','NE'):continue
            vals=[]
            for child in block:
                if local(child)!='inputVariables':continue
                for var in child:
                    if local(var)!='variable' or var.get('formalParameter')=='EN':continue
                    ex=[(''.join(x.itertext())).strip() for x in var.iter() if local(x)=='expression']
                    for c in var.iter():
                        if local(c)!='connection':continue
                        e=nodes.get(c.get('refLocalId'))
                        if e is not None:ex.extend((''.join(z.itertext())).strip() for z in e.iter() if local(z)=='expression')
                    vals.append(ex[0] if ex else '')
            if len(vals)>=2:
                for v,n in ((vals[0],vals[1]),(vals[1],vals[0])):
                    if RE_SIMPLE.fullmatch(v) and RE_INT.fullmatch(n):
                        d=observations[v];d['values'].add(int(n));d['compares'].add(int(n));d['sources'].add(owner)
    del root
    return observations,unit_count

def generate(src,out):
    ob,units=extract(src)
    controllers={v:d for v,d in ob.items() if len(d['values'])>=3 and len(d['compares'])>=3}
    rows=[]
    for v,d in sorted(controllers.items()):
        states=sorted(d['values']); observed=d['assigns']|d['compares']
        for i,a in enumerate(states):
            # Speculative interval between neighboring OBSERVED states, not a verified branch.
            if i+1<len(states):
                b=states[i+1]
                rows.append({'构成要素':';'.join(sorted(d['sources'])[:3]),'动作':'未确认阶段','动作描述':'相邻已观察状态；未验证程序控制流','开始标签':f'{v}={a}','结束标签':f'{v}={b}','状态':'adjacency_hypothesis','证据POU':';'.join(sorted(d['sources'])[:3]),'证据节点':'observed_states','原始条件':'','控制器':v,'分支数':0})
            # Explicit candidate exit to reset state only when 0 is observed. Do not claim it must happen.
            if a!=0 and 0 in observed:
                rows.append({'构成要素':';'.join(sorted(d['sources'])[:3]),'动作':'可能结束阶段','动作描述':'0状态已出现，是否由当前状态跳转尚待验证','开始标签':f'{v}={a}','结束标签':f'{v}=0','状态':'reset_hypothesis','证据POU':';'.join(sorted(d['sources'])[:3]),'证据节点':'observed_states','原始条件':'','控制器':v,'分支数':0})
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    fields=['构成要素','动作','动作描述','开始标签','结束标签','状态','证据POU','证据节点','原始条件','控制器','分支数']
    with (out/'recall_candidates.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    report={'source':str(src),'units':units,'controller_count':len(controllers),'candidate_count':len(rows),'warning':'These are adjacency/reset hypotheses, NOT validated state transitions or verified action coverage.','controllers':{v:{'states':sorted(d['values']),'assigned':sorted(d['assigns']),'compared':sorted(d['compares'])} for v,d in controllers.items()}}
    (out/'recall_evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('-o','--out',default='outputs/recall');a=p.parse_args();r=generate(a.source,a.out);print(json.dumps({k:v for k,v in r.items() if k!='controllers'},ensure_ascii=False))
