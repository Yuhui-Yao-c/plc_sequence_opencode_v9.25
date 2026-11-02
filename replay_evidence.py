#!/usr/bin/env python3
"""Compatibility replay when original XML is unavailable: does NOT reparse source."""
import argparse,json,csv
from pathlib import Path
FIELDS=['构成要素','动作','动作描述','开始标签','结束标签','状态','证据POU','证据节点','原始条件','控制器','分支数']
def replay(path,out):
 d=json.loads(Path(path).read_text(encoding='utf8'));out=Path(out);out.mkdir(parents=True,exist_ok=True)
 rows=[]
 for v,info in d['step_controllers'].items():
  states=sorted(info['states'])
  for a,b in zip(states,states[1:]):
   rows.append(dict(zip(FIELDS,['','未确认阶段','相邻已观察状态；非控制流证明',f'{v}={a}',f'{v}={b}','adjacency_hypothesis','cached_evidence','observed_states','',''+v,0])))
  if 0 in states:
   for a in states:
    if a!=0:rows.append(dict(zip(FIELDS,['','可能结束阶段','状态0已被观察，非控制流证明',f'{v}={a}',f'{v}=0','reset_hypothesis','cached_evidence','observed_states','',''+v,0])))
 with (out/'recall_candidates.csv').open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
 return len(rows)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('evidence');p.add_argument('-o','--out',required=True);a=p.parse_args();print('replayed',replay(a.evidence,a.out))
