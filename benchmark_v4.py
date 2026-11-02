"""Format-tolerant independent benchmark: never writes predictions or trains on truth."""
import argparse,csv,re,json
from pathlib import Path
from benchmark_v3 import assess,csvrows,md
SEP=re.compile(r'\t+')
def truth_rows(path):
 p=Path(path);lines=p.read_text(encoding='utf-8-sig').splitlines();r=[]
 if any('\t' in x for x in lines[:20]):
  for line in lines:
   cells=[x.strip().replace('\xa0',' ') for x in SEP.split(line)]
   if len(cells)>=3 and cells[1] and cells[2] and ('=' in cells[1] or 'StationMovingFlag' in cells[1]):
    r.append((cells[1],cells[2],cells[0]))
  return r
 return md(p)
def normalize(text):
 text=re.sub(r'(?i)\b(?:INT|DINT|UDINT|UINT|LINT|SINT|WORD)#','',text)
 text=re.sub(r'\s+','',text).upper()
 return text
STATE=re.compile(r'([\w\u0080-\uffff.]+(?:\[\d+\])?)\s*=\s*(-?\d+)')
def evaluate(pred,truth):
 pairs={(normalize(r['开始标签']),normalize(r['结束标签'])) for r in pred}
 states={};exits=set()
 for r in pred:
  for col in ('开始标签','结束标签'):
   for v,n in STATE.findall(normalize(r.get(col,''))):states.setdefault(v,set()).add(int(n))
  if r.get('状态')=='state_exit':exits.add(normalize(r['开始标签']))
 result=[]
 for idx,(a,b,desc) in enumerate(truth,1):
  an,bn=normalize(a),normalize(b);A=STATE.findall(an);B=STATE.findall(bn)
  sa=bool(A) and all(int(n) in states.get(v,set()) for v,n in A)
  sb=bool(B) and all(int(n) in states.get(v,set()) for v,n in B)
  exact=(an,bn) in pairs
  # Supports NOT of one or more start state clauses without incorrectly treating arbitrary NOT as equivalent.
  is_exit=bn.startswith('NOT') and bool(A) and all(f'{v}={n}' in exits for v,n in A) and all(f'{v}={n}' in bn for v,n in A)
  coarse=exact or is_exit
  result.append({'row':idx,'description':desc,'start':a,'end':b,'exact':exact,'state_exit_equivalent':is_exit,'supported_boundary_shape':coarse,'start_anchor':sa,'end_anchor':sb,'both_anchors':sa and sb,'status':'exact' if exact else 'state_exit_equivalent' if is_exit else 'anchors_only' if sa and sb else 'unresolved'})
 metrics={'truth_rows':len(result),'pred_rows':len(pred)}
 for k in ['exact','state_exit_equivalent','supported_boundary_shape','start_anchor','end_anchor','both_anchors']:
  metrics[k]=sum(bool(r[k]) for r in result)
 return {'metrics':metrics,'records':result,'caution':'Shape coverage is NOT verified physical-action or feasible control-path coverage. A NOT state exit is a boolean boundary, not proof of completion.'}
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('prediction');a.add_argument('truth');a.add_argument('-o','--out',default='benchmark_v4.json');args=a.parse_args();r=evaluate(csvrows(args.prediction),truth_rows(args.truth));Path(args.out).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(r['metrics'],ensure_ascii=False))
