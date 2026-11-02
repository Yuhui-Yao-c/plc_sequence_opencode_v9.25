#!/usr/bin/env python3
"""PLC sequence extraction (static, conservative). Supports PLCopen XML LD/FBD and ST.
Produces candidate intervals and evidence; never fabricates unknown signals.
"""
from __future__ import annotations
import argparse, csv, json, re, collections
from pathlib import Path
from lxml import etree

STEP_RE = re.compile(r'^[A-Za-z_\u0080-\uffff][\w.\[\],]*$')
ASSIGN_RE = re.compile(r'([\w.\[\],\u0080-\uffff]+)\s*:=\s*(-?\d+)\s*;', re.I)
COND_RE = re.compile(r'([\w.\[\],\u0080-\uffff]+)\s*(?:=|==)\s*(-?\d+)', re.I)
REV_COND_RE = re.compile(r'(?<![\w.])(-?\d+)\s*(?:=|==)\s*([\w.\[\],\u0080-\uffff]+)', re.I)
TOKEN_RE = re.compile(r'[\w.\[\],\u0080-\uffff]+')
COMMAND_WORDS = ('execute','start','run','move','act','cmd','trigger','open','close','clamp','vacuum','吸','夹','伸','缩','启动','运行','下降','上升','焊接','切胶')
FEEDBACK_WORDS = ('done','ok','ready','busy','reached','inpos','feedback','complete','finish','sensor','switch','到位','完成','反馈','检测')
WAIT_WORDS = ('wait','ready','allow','permit','signal','request','等待','允许','请求','就绪')
SKIP_WORDS = ('alarm','error','fault','reset','init','manual','hmi','safety','safe','报警','复位','初始化','手动')


def tag(e): return etree.QName(e).localname

def direct(e,name):
    return next((x for x in e if tag(x)==name), None)

def children(e,name): return [x for x in e if tag(x)==name]

def text_of(e,name):
    x=direct(e,name)
    return ''.join(x.itertext()).strip() if x is not None else ''

def normalize(s): return re.sub(r'\s+',' ',s or '').strip()

def refs(e):
    return [(x.get('refLocalId'),x.get('formalParameter')) for x in e.iter() if tag(x)=='connection' and x.get('refLocalId') is not None]

def parse_block(e):
    ins=[]; outs=[]
    for groupname,arr in [('inputVariables',ins),('inOutVariables',ins),('outputVariables',outs)]:
        group=direct(e,groupname)
        if group is None: continue
        for idx,var in enumerate(children(group,'variable')):
            arr.append({'index':idx,'pin':var.get('formalParameter',''),'refs':refs(direct(var,'connectionPointIn')) if direct(var,'connectionPointIn') is not None else [],'expr':text_of(var,'expression') or (text_of(direct(var,'connectionPointOut'),'expression') if direct(var,'connectionPointOut') is not None else '')})
    return ins,outs

def paths_to(node_id, nodes, visiting=None):
    """Best-effort conservative condition graph; branches are OR, serial contacts AND.
    Records uncertain function blocks without inventing their Boolean semantics."""
    visiting=set() if visiting is None else visiting
    if not node_id or node_id not in nodes or node_id in visiting: return ('?',True)
    e=nodes[node_id]; k=tag(e); visiting=visiting|{node_id}
    if k=='leftPowerRail': return ('TRUE',False)
    if k in ('inVariable','outVariable'): return (text_of(e,'expression') or '?',False)
    if k=='contact':
        term=text_of(e,'variable') or '?'
        if e.get('negated')=='true': term='NOT '+term
        if e.get('edge') in ('rising','falling'): term=f'{e.get("edge").upper()}({term})'
        parent=direct(e,'connectionPointIn'); links=refs(parent) if parent is not None else []
        pre,amb=combine(links,nodes,visiting)
        return (term if pre=='TRUE' else f'({pre}) AND {term}',amb)
    if k in ('branchTerminator','branchConnector','continuation'):
        p=direct(e,'connectionPointIn'); return combine(refs(p) if p is not None else [],nodes,visiting)
    if k=='block':
        kind=e.get('typeName','').upper(); inputs,_=parse_block(e)
        vals=[]; unknown=False
        for pin in inputs:
            if pin['pin']=='EN': continue
            if pin['expr']: vals.append(pin['expr'])
            elif pin['refs']:
                v,u=combine(pin['refs'],nodes,visiting); vals.append(v);unknown|=u
        if kind in ('EQ','NE','GT','GE','LT','LE') and len(vals)>=2:
            op={'EQ':'=','NE':'<>','GT':'>','GE':'>=','LT':'<','LE':'<='}[kind]
            return f'{vals[0]} {op} {vals[1]}',unknown
        if kind in ('AND','OR','XOR') and len(vals)>1: return ('('+f' {kind} '.join(vals)+')',unknown)
        if kind=='NOT' and vals: return ('NOT ('+vals[0]+')',unknown)
        en=next((p for p in inputs if p['pin']=='EN'),None)
        if en and en['refs']: return combine(en['refs'],nodes,visiting)
        return (f'UNRESOLVED_BLOCK({kind})',True)
    if k=='coil':
        parent=direct(e,'connectionPointIn');return combine(refs(parent) if parent is not None else [],nodes,visiting)
    return ('?',True)

def combine(links,nodes,visiting):
    if not links: return ('TRUE',False)
    results=[paths_to(ref,nodes,visiting) for ref,_ in links]
    unique=list(dict.fromkeys(s for s,_ in results))
    return (unique[0] if len(unique)==1 else '('+' OR '.join(unique)+')',any(u for _,u in results))

def value_of(ref,nodes):
    node=nodes.get(ref)
    if node is None:return None
    k=tag(node)
    if k in ('inVariable','outVariable'):return text_of(node,'expression')
    if k=='block':
        kind=node.get('typeName','').upper()
        if kind in ('INT_TO_DINT','TO_INT','TO_DINT'):
            ins,_=parse_block(node)
            vals=[value_of(r,nodes) for pin in ins for r,_ in pin['refs']]
            return vals[0] if len(vals)==1 else None
    return None

def source_xml(path):
    # XML parser prohibits remote DTD/entity resolution.
    parser=etree.XMLParser(resolve_entities=False,no_network=True,huge_tree=True,remove_comments=False)
    return etree.parse(str(path),parser)

def collect(source):
    root=source_xml(source)
    pou_list=[]; events=[]; comments=[]; unresolved=[]; raw_st=[]
    pou_list=[e.get('name','') for e in root.iter() if tag(e)=='pou']
    for unit in root.iter():
        if tag(unit) not in ('LD','FBD','ST'):continue
        ancestor=unit.getparent(); owner=None
        while ancestor is not None:
            if tag(ancestor) in ('action','pou'):
                owner=ancestor;break
            ancestor=ancestor.getparent()
        if owner is None:continue
        if tag(owner)=='action':
            loc=owner.get('name','')
        else:
            loc=owner.get('name','')+'/main'
        if tag(unit)=='ST':
            raw_st.append((loc,''.join(unit.itertext())))
            continue
        nodes={e.get('localId'):e for e in unit if e.get('localId') is not None}
        for e in unit:
            k=tag(e)
            if k=='comment':
                content=' '.join(''.join(z.itertext()) for z in e if tag(z)=='content')
                if content:comments.append({'pou':loc,'text':normalize(content),'id':e.get('localId')})
            if k=='coil':
                var=text_of(e,'variable'); condition,amb=paths_to(e.get('localId'),nodes)
                events.append({'pou':loc,'kind':'coil','target':var,'value':e.get('storage') or 'true','guard':condition,'uncertain':amb,'id':e.get('localId')})
            if k!='block':continue
            kind=e.get('typeName','').upper(); ins,outs=parse_block(e)
            if kind=='MOVE':
                input_vals=[]; en_guards=[]
                for pin in ins:
                    if pin['pin']=='EN':
                        for r,_ in pin['refs']:
                            en_guards.append(paths_to(r,nodes))
                    else:
                        vs=[value_of(r,nodes) for r,_ in pin['refs']]
                        input_vals.extend([x for x in vs if x is not None])
                        if pin['expr']:input_vals.append(pin['expr'])
                targets=[z['expr'] for z in outs if z['expr']]
                if len(input_vals)==1 and targets:
                    guard,unc=(en_guards[0] if len(en_guards)==1 else ('?',True))
                    for target in targets:events.append({'pou':loc,'kind':'assign','target':target,'value':input_vals[0],'guard':guard,'uncertain':unc,'id':e.get('localId')})
                elif targets:unresolved.append({'pou':loc,'id':e.get('localId'),'type':'ambiguous MOVE','targets':targets})
    for loc,st in raw_st:
        for m in ASSIGN_RE.finditer(st):
            events.append({'pou':loc,'kind':'assign','target':m.group(1),'value':m.group(2),'guard':'ST_UNRESOLVED','uncertain':True,'id':'ST'})
        # Structured text guarded assignments require statement parser; do not infer here.
        if ':=' in st and ('CASE ' in st.upper() or 'IF ' in st.upper()):unresolved.append({'pou':loc,'type':'ST requires control-flow parsing'})
    return pou_list,events,comments,unresolved

def choose_step_vars(events):
    by_var=collections.defaultdict(list)
    for e in events:
        if e['kind']=='assign' and re.fullmatch(r'-?\d+',str(e['value'])) and STEP_RE.fullmatch(e['target']):by_var[e['target']].append(e)
    # Require evidence of a multi-state controller; name patterns are weak ranking signals only.
    steps={}
    for var,es in by_var.items():
        values={int(e['value']) for e in es}
        if len(values)<3:continue
        guards=' '.join(e['guard'] for e in es)
        same_in_guards=sum(any(v==var for v,_ in eq_conditions(e['guard'])) for e in es)
        score=(len(values)>=4)+2*(same_in_guards>=2)+bool(re.search('step|seq|state|phase|步序|步骤',var,re.I))
        if score>=2:steps[var]={'states':sorted(values),'score':score,'assignments':es}
    return steps

def eq_conditions(condition):
    return COND_RE.findall(condition) + [(v,n) for n,v in REV_COND_RE.findall(condition)]

def infer(events,comments,step_vars):
    by_pou=collections.defaultdict(list)
    for e in events:by_pou[e['pou']].append(e)
    transitions=[];state_events=collections.defaultdict(list)
    for e in events:
        if e['kind'] != 'coil': continue
        for key,value in eq_conditions(e['guard']):
            if key in step_vars: state_events[(key,int(value))].append(e)
    for var,info in step_vars.items():
        for a in info['assignments']:
            guards=eq_conditions(a['guard'])
            sources=[(v,int(n)) for v,n in guards if v==var]
            if sources:
                for _,src in sources:
                    transitions.append({'controller':var,'from':src,'to':int(a['value']),'guard':a['guard'],'pou':a['pou'],'evidence':a['id'],'uncertain':a['uncertain'] or ' OR ' in a['guard']})
            else:
                transitions.append({'controller':var,'from':None,'to':int(a['value']),'guard':a['guard'],'pou':a['pou'],'evidence':a['id'],'uncertain':True})
    transition_by_state=collections.defaultdict(list)
    for t in transitions:
        if t['from'] is not None:transition_by_state[(t['controller'],t['from'])].append(t)
    rows=[]
    for (var,state),outputs in sorted(state_events.items()):
        viable=[]
        for e in outputs:
            if e['value'] in ('reset','false'):continue
            name=e['target'].lower()
            if any(w in name for w in SKIP_WORDS):continue
            viable.append(e)
        if not viable:continue
        trs=transition_by_state[(var,state)]
        end_states=sorted(set(t['to'] for t in trs if t['to']!=state))
        if len(end_states)==1:end=f'{var}={end_states[0]}'; status='step_exit_observed'
        elif len(end_states)>1:end=' OR '.join(f'{var}={x}' for x in end_states);status='branch_review'
        else:end='';status='no_verified_exit'
        for e in viable:
            label=e['target']; low=label.lower()
            if any(w in low for w in COMMAND_WORDS):typ='execute'
            elif any(w in low for w in FEEDBACK_WORDS):typ='feedback_or_handshake'
            elif any(w in low for w in WAIT_WORDS):typ='handshake'
            else:typ='unknown_output'
            if typ=='unknown_output':continue
            rows.append({'构成要素':e['pou'].split('/')[0],'动作':label,'动作描述':typ,'开始标签':f'{var}={state}','结束标签':end,'状态':status if not e['uncertain'] else 'needs_logic_review','证据POU':e['pou'],'证据节点':e['id'],'原始条件':e['guard'],'控制器':var,'分支数':len(end_states)})
    # Additionally include step-only candidates for states with no classified outputs: prevents silent omission.
    covered={(r['控制器'],int(r['开始标签'].rsplit('=',1)[-1])) for r in rows}
    for var,info in step_vars.items():
        for state in info['states']:
            if (var,state) in covered:continue
            trs=transition_by_state[(var,state)]
            end_states=sorted({t['to'] for t in trs if t['to']!=state})
            if not trs:continue
            rows.append({'构成要素':info['assignments'][0]['pou'].split('/')[0],'动作':'待识别工艺阶段','动作描述':'需结合命令、注释和上下文确认','开始标签':f'{var}={state}','结束标签':' OR '.join(f'{var}={n}' for n in end_states),'状态':'needs_semantic_review','证据POU':trs[0]['pou'],'证据节点':trs[0]['evidence'],'原始条件':trs[0]['guard'],'控制器':var,'分支数':len(end_states)})
    rows.sort(key=lambda r:(r['构成要素'],r['控制器'],int(r['开始标签'].rsplit('=',1)[-1]),r['动作']))
    return rows,transitions

def run(input_path, out_dir):
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    pous,events,comments,unresolved=collect(input_path)
    steps=choose_step_vars(events)
    rows,transitions=infer(events,comments,steps)
    cols=['构成要素','动作','动作描述','开始标签','结束标签','状态','证据POU','证据节点','原始条件','控制器','分支数']
    with (out/'sequence_candidates.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
    report={'source':str(input_path),'pou_count':len(set(pous)),'events':len(events),'step_controllers':{v:{'states':obj['states'],'score':obj['score']} for v,obj in steps.items()},'candidate_count':len(rows),'transitions':transitions,'unresolved':unresolved,'candidates':rows,'comments':comments}
    (out/'evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    metrics={'pous':len(set(pous)),'events':len(events),'controllers':len(steps),'candidates':len(rows),'unresolved':len(unresolved)}
    from enhance import postprocess
    audit=postprocess(out)
    metrics.update({'unique_candidates':audit['raw_unique'],'optimized_candidates':audit['optimized_rows'],'event_candidates':audit['derived_event_candidates']})
    print(json.dumps(metrics,ensure_ascii=False));return metrics

if __name__=='__main__':
    ap=argparse.ArgumentParser(description='通用PLC静态步序候选提取器')
    ap.add_argument('source',help='PLCopen XML文件，可为.xml或.txt')
    ap.add_argument('-o','--out',default='result',help='输出目录')
    args=ap.parse_args();run(args.source,args.out)
