"""Source-only action graph; every edge records its evidence strength.
No project-specific symbol, station, or step constant is used in extraction.
"""
import csv,json,re,collections
from pathlib import Path
from lxml import etree
from smc2_adapter import networks,integer,IDENT
from recall import local,RE_SIMPLE,RE_INT
FIELDS=['source_format','source_unit','network','guard','command','arguments','feedback','next_state','relation','evidence','comment']
GENERIC={'MOVE','EQ','NE','=','<>','>=','<=','>','<','AND','OR','NOT','TON','TOF','TP','UP','DOWN','INC','CLEAR','ARYMOVE','ARYSHL','SETBLOCK','DINT_TO_REAL','ADD','SUB','MUL','DIV'}
FB_HINT=re.compile(r'(MOVE|CONTROL|MOTION|CY|ROBOT|VACUUM|AXIS|CLAMP|SERVO|GRIP|LASER|CAMERA|SCAN|WELD|CUT|PRESS|PUMP|BLOW|HEAT|GLUE|FEED|PICK|PLACE|取料|放料|贴胶)',re.I)
FEEDBACK=re.compile(r'(DONE|FINISH|READY|POSOK|COMPLETE|INPLACE|HOMED|REACHED|SENSOR|SIGNAL|CLOSED|OPENED|到位|完成)',re.I)
BOOL=re.compile(r'(START|EXECUTE|MOVE|OUTPUT|COMMAND|CMD|CLAMP|GRIP|VACUUM|SUCTION|VALVE|SIGNAL|TRIGGER|CYLINDER|MOTOR|ACTION|取料|启动)',re.I)
STATE_RE=re.compile(r'([^\s=]+)\s*=\s*(-?\d+)')

def smc2_events(source):
  for _,entry in etree.iterparse(str(source),events=('end',),tag='entry',huge_tree=True,resolve_entities=False,no_network=True):
    if entry.get('type')=='json':
      for ix,net in enumerate(networks(entry.text or '')):
        nodes=net.get('CLs',[]);cmp=[];writes=[];feedback=[];cmds=[]
        for n in nodes:
          typ=n.get('__type'); name=str(n.get('Name','')).lstrip('@'); nn=name.upper()
          ins={str(p.get('Arg')):str(p.get('Var','')).strip() for p in n.get('In',[]) if p.get('__type')=='PRM'}
          if typ=='F' and nn in ('=','EQ','=='):
            a,b=ins.get('In1',''),ins.get('In2','');av,bv=integer(a),integer(b)
            if av is None and bv is not None and IDENT.fullmatch(a):cmp.append((a,bv))
            if bv is None and av is not None and IDENT.fullmatch(b):cmp.append((b,av))
          if typ=='F' and nn=='MOVE':
            val=integer(ins.get('In',''))
            if val is not None:
              for p in n.get('Out',[]):
                if p.get('Arg')=='Out' and p.get('Var'):writes.append((str(p['Var']),val))
          if typ=='LD' and n.get('Var') and FEEDBACK.search(str(n['Var'])):
            feedback.append(('NOT ' if n.get('Not') else '')+str(n['Var']))
          if typ in ('F','FB') and nn not in GENERIC:
            args=';'.join(f'{k}={v}' for k,v in ins.items() if v and k.upper() not in ('EN','ALARM','AUTO_MANUAL'))
            cmds.append((name,args))
          if typ=='ST' and n.get('Var') and BOOL.search(str(n['Var'])):
            cmds.append(('coil'+('_SET' if n.get('S') else '_RESET' if n.get('RS') else ''),str(n['Var'])))
          if typ=='IST' and n.get('TXT'):
            txt=str(n['TXT'])
            for m in re.finditer(r'([\w.\[\]]+)\s*:=\s*(TRUE|FALSE)\s*;',txt,re.I):
              if BOOL.search(m.group(1)):cmds.append(('ST_assignment',m.group(0)))
        guard=' OR '.join(f'{v}={i}' for v,i in dict.fromkeys(cmp))
        nxt=' OR '.join(f'{v}={i}' for v,i in dict.fromkeys(writes) if any(v==x for x,_ in cmp))
        for name,args in cmds:
          yield ['smc2',entry.get('name',''),ix,guard,name,args,' AND '.join(dict.fromkeys(feedback)),nxt,'same_network_observation','co_location_only',str(net.get('CMT',''))[:220]]
    entry.clear()
    while entry.getprevious() is not None:del entry.getparent()[0]

def expressions(var,nodes):
  vals=[]
  for z in var.iter():
    if local(z)=='expression':vals.append(''.join(z.itertext()).strip())
    if local(z)=='connection':
      ref=nodes.get(z.get('refLocalId'))
      if ref is not None:
        for q in ref.iter():
          if local(q)=='expression':vals.append(''.join(q.itertext()).strip())
  return list(dict.fromkeys(x for x in vals if x))

def plcopen_events(source):
  # iterparse per body to avoid whole-tree memory pressure; nested action LD/FBD also visited.
  for _,unit in etree.iterparse(str(source),events=('end',),tag=('{*}LD','{*}FBD','{*}ST'),huge_tree=True,resolve_entities=False,no_network=True):
    owner='unknown';p=unit.getparent()
    while p is not None:
      if local(p) in ('action','pou'):
        owner=p.get('name') or 'unknown';break
      p=p.getparent()
    if local(unit)=='ST':
      txt=''.join(unit.itertext());guards=[]
      for m in re.finditer(r'([\w.\[\]]+)\s*=\s*(-?\d+)',txt):guards.append(f'{m.group(1)}={m.group(2)}')
      guard=' OR '.join(dict.fromkeys(guards[:15]))
      for m in re.finditer(r'([\w.\[\]]+)\s*:=\s*(TRUE|FALSE)\s*;',txt,re.I):
        if BOOL.search(m.group(1)):yield ['plcopen',owner,'ST',guard,'ST_assignment',m.group(0),'','','ST_text_observation','text_scope_only','']
      unit.clear();continue
    nodes={e.get('localId'):e for e in unit if e.get('localId')}
    guards=[];feedback=[];commands=[];moves=[]
    for node in unit:
      typ=local(node)
      if typ=='block':
        name=node.get('typeName','');inputs={};outputs={}
        for grp in node:
          side=local(grp)
          if side not in ('inputVariables','outputVariables','inOutVariables'):continue
          target=inputs if side=='inputVariables' else outputs
          for var in grp:
            if local(var)=='variable':target[var.get('formalParameter','')]=expressions(var,nodes)
        if name.upper() in ('EQ','NE'):
          vals=[x[0] for k,x in inputs.items() if x and k.upper()!='EN']
          if len(vals)>=2:
            for v,n in ((vals[0],vals[1]),(vals[1],vals[0])):
              if RE_SIMPLE.fullmatch(v) and RE_INT.fullmatch(n):guards.append(f'{v}={n}')
        if name.upper()=='MOVE':
          for v in [x for seq in outputs.values() for x in seq]:
            for num in [x for seq in inputs.values() for x in seq]:
              if RE_SIMPLE.fullmatch(v) and RE_INT.fullmatch(num):moves.append(f'{v}={num}')
        elif name.upper() not in GENERIC:
          args=';'.join(f'{k}={"|".join(vals)}' for k,vals in inputs.items() if vals and k.upper()!='EN')
          commands.append((name,args))
      if typ=='contact':
        for v in node.iter():
          if local(v)=='variable':
            ss=''.join(v.itertext()).strip()
            if FEEDBACK.search(ss):feedback.append(('NOT ' if node.get('negated')=='true' else '')+ss)
      if typ=='coil':
        for v in node.iter():
          if local(v)=='variable':
            ss=''.join(v.itertext()).strip()
            if BOOL.search(ss):commands.append(('coil',ss))
    guard=' OR '.join(dict.fromkeys(guards[:20]));target=' OR '.join(dict.fromkeys(moves[:20]))
    for name,args in commands:
      yield ['plcopen',owner,unit.get('localId',''),guard,name,args,' AND '.join(dict.fromkeys(feedback)),target,'same_body_observation','co_location_only','']
    unit.clear()

def generate(source,out,fmt):
  out=Path(out);out.mkdir(parents=True,exist_ok=True);path=out/'event_graph.csv';counts=collections.Counter()
  with path.open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f);w.writerow(FIELDS)
    for row in (smc2_events(source) if fmt=='smc2' else plcopen_events(source)):
      w.writerow(row);counts[row[4]]+=1
  info={'events':sum(counts.values()),'command_kinds':len(counts),'caveat':'Relations are only source co-location, NOT validated causal edges.'}
  (out/'event_graph_audit.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf8')
  return info
