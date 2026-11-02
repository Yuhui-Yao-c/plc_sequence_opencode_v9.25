"""Conservative benchmark: exact or explicit Boolean exit; never call anchors action recall."""
import argparse,csv,json,re
from pathlib import Path
from benchmark_v4 import truth_rows,normalize,STATE
from benchmark_v3 import csvrows

def clean(expr):
 expr=normalize(expr).replace('==','=')
 # strip redundant balanced outer parentheses
 while expr.startswith('(') and expr.endswith(')'):
  depth=0;valid=True
  for c in expr[:-1]:
   depth += (c=='(')-(c==')')
   if depth==0:valid=False;break
  if not valid:break
  expr=expr[1:-1]
 return expr

def clauses(s):
 """Only homogeneous same-controller OR of equality atoms; no inference of mixed logic."""
 s=clean(s)
 if 'AND' in s or 'NOT' in s:return None
 parts=s.split('OR');vals=[]
 for p in parts:
  m=STATE.fullmatch(clean(p))
  if not m:return None
  vals.append((m.group(1),int(m.group(2))))
 return tuple(sorted(set(vals))) if len(set(v for v,n in vals))==1 else None

def exit_equivalent(a,b,exits):
 atoms=clauses(a)
 if not atoms or not all(f'{v}={n}' in exits for v,n in atoms):return False
 # Accept NOT of the entire OR or AND of NOTs, by De Morgan, using exact atom set.
 bn=clean(b)
 if bn.startswith('NOT(') and bn.endswith(')') and clauses(bn[4:-1])==atoms:
  return True
 parts=re.split('AND',bn)
 if len(parts)==len(atoms) and len(parts)>1:
  found=[]
  for p in parts:
   p=clean(p)
   if not p.startswith('NOT'):return False
   expr=clean(p[3:]);m=STATE.fullmatch(expr)
   if not m:return False
   found.append((m.group(1),int(m.group(2))))
  return tuple(sorted(found))==atoms
 if len(atoms)==1 and bn.startswith('NOT'):
  return clauses(clean(bn[3:]))==atoms
 return False

def evaluate(pred,truth):
 pairs={(normalize(r['开始标签']),normalize(r['结束标签'])) for r in pred}
 st=set();exits=set();graph={}
 for r in pred:
  a,b=normalize(r.get('开始标签','')),normalize(r.get('结束标签',''))
  for s in (a,b):
   for v,n in STATE.findall(s):st.add((v,int(n)))
  if r.get('状态')=='state_exit':exits.add(a)
  if r.get('状态') in ('observed_same_network_transition','source_transition'):
   ca,cb=clauses(a),clauses(b)
   if ca and cb and len(ca)==len(cb)==1 and ca[0][0]==cb[0][0]:graph.setdefault(ca[0],set()).add(cb[0])
 results=[]
 for i,(a,b,desc) in enumerate(truth,1):
  A=clauses(a);B=clauses(b)
  exact=(normalize(a),normalize(b)) in pairs
  x=exit_equivalent(a,b,exits)
  start_anchor=bool(A) and all(k in st for k in A)
  end_anchor=bool(B) and all(k in st for k in B)
  source_path=False
  if A and B and len(A)==len(B)==1:
   cur=A[0];dest=B[0];queue=[cur];seen={cur}
   while queue and not source_path:
    cur=queue.pop(0)
    for nx in graph.get(cur,()):
     if nx==dest:source_path=True;break
     if nx not in seen:seen.add(nx);queue.append(nx)
  status='exact' if exact else 'boolean_exit' if x else 'source_network_path' if source_path else 'anchors_only' if start_anchor and end_anchor else 'unresolved'
  results.append({'row':i,'description':desc,'start':a,'end':b,'exact':exact,'boolean_exit':x,'source_network_path':source_path,'start_anchor':start_anchor,'end_anchor':end_anchor,'status':status})
 metrics={'truth_rows':len(results),'prediction_rows':len(pred),'exact':sum(r['exact'] for r in results),'boolean_exit':sum(r['boolean_exit'] for r in results),'shape_supported':sum(r['exact'] or r['boolean_exit'] for r in results),'source_network_path':sum(r['source_network_path'] for r in results),'both_anchors':sum(r['start_anchor'] and r['end_anchor'] for r in results),'unresolved':sum(r['status']=='unresolved' for r in results)}
 return {'metrics':metrics,'records':results,'limits':'Exact match and Boolean state exit do not prove physical action. Same-network graph paths are tentative control flow, not reachability proofs. No general 100%-recall claim.'}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('prediction');p.add_argument('truth');p.add_argument('-o','--out',default='benchmark_v5.json');a=p.parse_args()
 r=evaluate(csvrows(a.prediction),truth_rows(a.truth));Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(r['metrics'],ensure_ascii=False))
