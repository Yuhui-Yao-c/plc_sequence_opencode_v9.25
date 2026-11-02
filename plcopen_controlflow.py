"""Conservative PLCopen LD/FBD MOVE enable-pin backtrace.
Only explicit refLocalId wiring can justify a relation; incomplete/ambiguous paths are reported.
No project-specific symbols or human answer file is used.
"""
import csv, json, re
from collections import defaultdict
from pathlib import Path
from lxml import etree
from recall import local, RE_SIMPLE

NUMBER=re.compile(r'^(?:[A-Z]+#)?(-?\d+)$',re.I)
FIELDS=['source_unit','body_kind','move_id','state_variable','source_state','target_state','feedback','other_guards','evidence','proof_level','caveat']

def expression(node):
    for e in node.iter():
        if local(e)=='expression':
            val=''.join(e.itertext()).strip()
            if val:return val
    return ''

def links(node):
    return [e.get('refLocalId') for e in node.iter() if local(e)=='connection' and e.get('refLocalId')]

def pin(block,kind,name):
    for section in block:
        if local(section)!=kind:continue
        for var in section:
            if local(var)=='variable' and (var.get('formalParameter') or '').upper()==name.upper():return var
    return None

def get_value(node,nodes):
    v=expression(node)
    if v:return v
    refs=links(node)
    if len(refs)==1 and refs[0] in nodes:return expression(nodes[refs[0]])
    return ''

def cmp_atom(block,nodes):
    if block.get('typeName','').upper() not in ('EQ','=','=='):return None
    vs=[]
    for sec in block:
        if local(sec)!='inputVariables':continue
        for p in sec:
            if local(p)=='variable' and (p.get('formalParameter') or '').upper()!='EN':vs.append(get_value(p,nodes))
    if len(vs)<2:return None
    for a,b in ((vs[0],vs[1]),(vs[1],vs[0])):
        m=NUMBER.fullmatch(b)
        if m and RE_SIMPLE.fullmatch(a):return (a,int(m.group(1)))
    return None

def control_inputs(node):
    kind=local(node)
    if kind=='block':
        if node.get('typeName','').upper() in ('EQ','NE','GT','GE','LT','LE'):
            # Boolean compare output is conditioned by its own EN if present; the predicate itself is a guard.
            return []
        return links(pin(node,'inputVariables','EN')) if pin(node,'inputVariables','EN') is not None else []
    if kind in ('contact','coil'):
        return links(next((e for e in node if local(e)=='connectionPointIn'),node))
    return links(node)

def scan(src):
    rows=[]; total_moves=0; unresolved=0
    for _,unit in etree.iterparse(str(src),events=('end',),tag=('{*}LD','{*}FBD'),huge_tree=True):
        parent=unit.getparent();owner='unknown'
        p=parent
        while p is not None:
            if local(p) in ('pou','action'):
                owner=p.get('name') or 'unknown';break
            p=p.getparent()
        nodes={n.get('localId'):n for n in unit if n.get('localId')}
        for move in unit:
            if local(move)!='block' or move.get('typeName','').upper()!='MOVE':continue
            total_moves+=1
            inp=pin(move,'inputVariables','IN');out=pin(move,'outputVariables','OUT')
            if inp is None or out is None:continue
            val=get_value(inp,nodes);target=get_value(out,nodes)
            m=NUMBER.fullmatch(val)
            if not m or not RE_SIMPLE.fullmatch(target):continue
            en=pin(move,'inputVariables','EN'); start=links(en) if en is not None else []
            stack=list(start);visited=set();atoms=[];signals=[];unknown=[]
            while stack:
                ref=stack.pop()
                if ref in visited:continue
                visited.add(ref)
                n=nodes.get(ref)
                if n is None:
                    unknown.append('missing_ref:'+ref);continue
                kind=local(n)
                if kind=='block':
                    atom=cmp_atom(n,nodes)
                    if atom:atoms.append(atom)
                    elif n.get('typeName','').upper() in ('OR','NOT','NE'):
                        unknown.append('boolean_branch:'+n.get('typeName',''))
                    elif n.get('typeName','').upper() not in ('AND','EQ'):
                        unknown.append('block:'+n.get('typeName',''))
                    stack.extend(control_inputs(n))
                elif kind=='contact':
                    v=expression(n)
                    if v:signals.append(('NOT ' if n.get('negated')=='true' else '')+v)
                    else:unknown.append('contact_expression_missing')
                    stack.extend(control_inputs(n))
                else:stack.extend(control_inputs(n))
            matching=sorted(set(n for v,n in atoms if v==target))
            other=sorted({v+'='+str(n) for v,n in atoms if v!=target}|set(signals))
            proof='wired_guard_incomplete' if unknown or not matching else 'wired_guard_local'
            if not matching:
                unresolved+=1
            for state in matching or [None]:
                rows.append({'source_unit':owner,'body_kind':local(unit),'move_id':move.get('localId',''),
                             'state_variable':target,'source_state':'' if state is None else state,
                             'target_state':int(m.group(1)),'feedback':' AND '.join(signals),
                             'other_guards':' AND '.join(other),'evidence':';'.join(sorted(visited)),
                             'proof_level':proof,'caveat':'EN线连接的局部依赖，非PLC扫描时序或真实动作完成证明；OR/NOT块需进一步还原' if proof=='wired_guard_local' else 'EN链中存在未解析节点或没有本状态比较；不当作已证明迁移'})
        unit.clear()
        while unit.getprevious() is not None:del unit.getparent()[0]
    return rows,{'moves_visited':total_moves,'relations':len(rows),'without_same_variable_guard':unresolved}

def generate(src,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    rows,summary=scan(src)
    with (out/'plcopen_control_relations.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
    (out/'plcopen_control_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary
