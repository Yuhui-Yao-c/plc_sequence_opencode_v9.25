"""Source-only generic action invocation inventory for embedded JSON ladder networks.
Never consults manual annotations. Detects parameterized FB/F calls, coils and feedback.
"""
import csv,json,re
from collections import defaultdict
from pathlib import Path
from lxml import etree
from smc2_adapter import networks,integer,IDENT
STATE_NAMES={'MOVE','EQ','NE','=', '<>', '>=','<=','>','<','AND','OR','NOT','TON','TOF','TP','UP','DOWN','INC','CLEAR','ARYMOVE','ARYSHL','SETBLOCK','DINT_TO_REAL'}
SIGNAL_RE=re.compile(r'(done|finish|ready|posok|complete|inplace|homed|busy|sensor|signal|到位|完成)',re.I)
def scan(path,out):
 out=Path(out);out.mkdir(parents=True,exist_ok=True)
 events=[];net_index=0;controllers=defaultdict(set)
 for _,e in etree.iterparse(str(path),events=('end',),tag='entry',huge_tree=True,resolve_entities=False,no_network=True):
  if e.get('type')=='json':
   ref=e.get('name','')
   for k,net in enumerate(networks(e.text or '')):
    net_index+=1;nodes=net.get('CLs',[]);states=[];commands=[];feedback=[];writes=[]
    for node in nodes:
     typ=node.get('__type','');name=str(node.get('Name','')).lstrip('@');ins={str(x.get('Arg','')):str(x.get('Var','')).strip() for x in node.get('In',[]) if x.get('__type')=='PRM'}
     if typ=='F' and name in ('=','EQ','=='):
      a,b=ins.get('In1',''),ins.get('In2','');an,bn=integer(a),integer(b)
      if an is None and bn is not None and IDENT.fullmatch(a):states.append((a,bn))
      if bn is None and an is not None and IDENT.fullmatch(b):states.append((b,an))
     if typ=='F' and name.upper()=='MOVE':
      vals=[str(x.get('Var','')).strip() for x in node.get('Out',[]) if x.get('__type')=='PRM' and x.get('Arg')=='Out']
      for dest in vals:
       if dest: writes.append(dest)
     if typ=='LD':
      var=str(node.get('Var',''))
      if SIGNAL_RE.search(var):feedback.append(('NOT ' if node.get('Not') else '')+var)
     if typ in ('F','FB') and name.upper() not in STATE_NAMES:
      operands=[]
      for x in node.get('In',[]):
       if x.get('__type')=='PRM' and x.get('Var'):
        arg=str(x.get('Arg',''));val=str(x.get('Var',''))
        if not re.search(r'(^EN$|ALARM|TIMEOUT|VELOCITY|AUTO_MANUAL)',arg,re.I):operands.append(f'{arg}={val}')
      commands.append((name,';'.join(operands[:12])))
     if typ=='ST' and node.get('Var') and not re.search(r'\b(safety|alarm|jam|suspended)\b',str(node['Var']),re.I):
      # Coil is an event candidate, not automatically a physical action.
      commands.append(('coil'+('_set' if node.get('S') else '_reset' if node.get('RS') else ''),str(node['Var'])))
    if states or commands:
     for var,step in states:controllers[var].add(step)
     for cmd,parameters in commands:
      ev={'source_entry':ref,'network_index':k,'source_network':net_index,'network_comment':str(net.get('CMT',''))[:250],
          'state_guard':' OR '.join(f'{v}={n}' for v,n in dict.fromkeys(states)),
          'command_type':cmd,'command_arguments':parameters,'feedback_observed':';'.join(dict.fromkeys(feedback))[:600],
          'network_writes':';'.join(dict.fromkeys(writes))[:400],
          'evidence_level':'same_network_only','warning':'No execution-path proof or FB completion semantics'}
      events.append(ev)
  e.clear()
  while e.getprevious() is not None:del e.getparent()[0]
 fieldnames=list(events[0]) if events else ['source_entry','network_index','source_network','network_comment','state_guard','command_type','command_arguments','feedback_observed','network_writes','evidence_level','warning']
 with (out/'action_event_inventory.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fieldnames);w.writeheader();w.writerows(events)
 result={'scanned_networks':net_index,'event_candidates':len(events),'controllers_with_event_guards':len(controllers),'unsupported':'Events are co-located with state comparisons; causal enable and completion are not proven'}
 (out/'action_event_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
 return result
