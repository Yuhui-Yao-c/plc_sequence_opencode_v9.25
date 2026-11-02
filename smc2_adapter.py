"""Vendor-neutral adapter for XML containers whose entries embed JSON LD/FBD networks.
No project-specific variable names, state values, or truth labels.
"""
import collections,csv,json,re
from pathlib import Path
from lxml import etree
IDENT=re.compile(r'^[^\s=<>:]+(?:\[[^\]]+\])?$')
NUM=re.compile(r'^(?:INT|DINT|UINT|UDINT|LINT|SINT|USINT|WORD|DWORD|LWORD)#\s*(-?\d+)$|^(-?\d+)$',re.I)
def integer(v):
 m=NUM.fullmatch(str(v).strip())
 return int(m.group(1) or m.group(2)) if m else None
def networks(text):
 d=json.JSONDecoder();i=0
 while i<len(text):
  while i<len(text) and (text[i].isspace() or text[i]==','):i+=1
  if i>=len(text):break
  try:obj,j=d.raw_decode(text,i)
  except json.JSONDecodeError:break
  i=j
  if isinstance(obj,dict) and isinstance(obj.get('CLs'),list):yield obj

def extract(source):
 obs=collections.defaultdict(lambda:{'compares':set(),'assigns':set(),'sources':set(),'links':set(),'feedback':set(),'descriptions':set(),'state_context':collections.defaultdict(list)})
 nets=0;entries=0
 for _,e in etree.iterparse(str(source),events=('end',),tag='entry',huge_tree=True,resolve_entities=False,no_network=True):
  entries+=1;ref=e.get('name','')
  if e.get('type')=='json':
   for k,net in enumerate(networks(e.text or '')):
    nets+=1; nodes=net.get('CLs',[]);desc=str(net.get('CMT','')).strip()[:180]; comparisons=[];writes=[];sensors=[]
    for node in nodes:
     typ=node.get('__type','');nam=str(node.get('Name','')).upper().lstrip('@')
     if typ=='LD' and node.get('Var') and node.get('Var') not in ('P_On','P_Off'):
      sensors.append((str(node['Var']),bool(node.get('Not'))))
     if typ=='F' and nam in ('=','==','EQ'):
      ps={p.get('Arg'):str(p.get('Var','')).strip() for p in node.get('In',[]) if p.get('__type')=='PRM'}
      a=ps.get('In1','');b=ps.get('In2','');av,bv=integer(a),integer(b)
      if av is None and bv is not None and IDENT.fullmatch(a):comparisons.append((a,bv))
      if bv is None and av is not None and IDENT.fullmatch(b):comparisons.append((b,av))
     if typ=='F' and nam=='MOVE':
      ins=[str(p.get('Var','')).strip() for p in node.get('In',[]) if p.get('__type')=='PRM' and p.get('Arg','').lower() not in ('en','eno')]
      outs=[str(p.get('Var','')).strip() for p in node.get('Out',[]) if p.get('__type')=='PRM' and p.get('Arg','').lower() not in ('en','eno')]
      for a in ins:
       v=integer(a)
       if v is not None:
        for dest in outs:
         if dest and IDENT.fullmatch(dest):writes.append((dest,v))
    for v,n in comparisons:
     d=obs[v];d['compares'].add(n);d['sources'].add(ref);d['state_context'][n].append((ref,k,desc,'comparison'))
     if desc:d['descriptions'].add(desc)
     for dest,target in writes:
      if dest==v:d['links'].add((n,target))
     for sig,neg in sensors:
      if sig!=v:d['feedback'].add((n,sig,neg))
    for v,n in writes:
     d=obs[v];d['assigns'].add(n);d['sources'].add(ref);d['state_context'][n].append((ref,k,desc,'assignment'))
     if desc:d['descriptions'].add(desc)
  e.clear()
  while e.getprevious() is not None:del e.getparent()[0]
 return obs,{'entries':entries,'networks':nets}

def generate(source,out):
 obs,meta=extract(source);out=Path(out);out.mkdir(parents=True,exist_ok=True)
 ctrls={v:d for v,d in obs.items() if len(d['compares'])>=2 and len(d['compares']|d['assigns'])>=3}
 rows=[];seen=set()
 def add(v,a,b,kind,ref,description=''):
  key=(v,a,b,kind)
  if key in seen:return
  seen.add(key);rows.append({'构成要素':v,'动作':'待确认','动作描述':description,'开始标签':a,'结束标签':b,'状态':kind,'证据POU':ref,'证据节点':'embedded_json_network','原始条件':'','控制器':v,'分支数':0})
 for v,d in sorted(ctrls.items()):
  st=sorted(d['compares']|d['assigns']);owner=';'.join(sorted(d['sources'])[:2]);ds=';'.join(sorted(d['descriptions'])[:2])
  def local(a):
   ctx=d['state_context'].get(a,[])
   compares=[x for x in ctx if x[3]=='comparison'];ctx=compares or ctx
   return ';'.join(dict.fromkeys(x[0] for x in ctx)), ';'.join(dict.fromkeys(x[2] for x in ctx if x[2]))[:600]
  for a in st:
   start=f'{v}={a}'
   # Explicit state exit is a logically valid boundary, even if successor is not known.
   ref,desc=local(a);add(v,start,f'NOT ({start})','state_exit',ref,desc)
  for a,b in sorted(d['links']):
   if a==b:continue
   ref,desc=local(a);add(v,f'{v}={a}',f'{v}={b}','observed_same_network_transition',ref,desc)
  for i,a in enumerate(st[:-1]):
   add(v,f'{v}={a}',f'{v}={st[i+1]}','adjacency_hypothesis',owner)
  for a,sig,neg in sorted(d['feedback']):
   if any(tok in sig.lower() for tok in ('done','finish','posok','ready','complete','signal','inplace')):
    add(v,f'{v}={a}',f'{v}={a} AND '+('NOT ' if neg else '')+sig,'feedback_hypothesis',owner)
 fields=['构成要素','动作','动作描述','开始标签','结束标签','状态','证据POU','证据节点','原始条件','控制器','分支数']
 with (out/'recall_candidates.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
 ev={'source_format':'smc2_embedded_json','controller_count':len(ctrls),'candidate_count':len(rows),**meta,'warning':'state-exit is supported as a boolean boundary; time alignment, event causality, state transition control flow and action semantics are not fully proven','controllers':{v:{'states':sorted(d['compares']|d['assigns']),'compared':sorted(d['compares']),'assigned':sorted(d['assigns']),'same_network_links':sorted([list(x) for x in d['links']])} for v,d in ctrls.items()}}
 (out/'recall_evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2),encoding='utf8')
 return ev
