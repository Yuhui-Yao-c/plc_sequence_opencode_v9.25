"""Per-gold-row transparent matching; only explicit sourced edges permit multi-step status.
A same-network edge is labeled hypothesis and never claimed as verified execution.
"""
import argparse,csv,json,collections
from pathlib import Path
from benchmark_v5 import clauses,exit_equivalent
from benchmark_v4 import truth_rows as _truth_rows,normalize
def truth_rows(path):
 if str(path).lower().endswith(".csv"):
  with open(path,encoding="utf-8-sig",newline="") as f:
   return [(r["开始标签"],r["结束标签"],r.get("动作描述","")) for r in csv.DictReader(f) if r.get("开始标签") and r.get("结束标签")]
 return _truth_rows(path)
from benchmark_v3 import csvrows

def evaluate(pred,truth):
 indexed=collections.defaultdict(list);exits=set();graph=collections.defaultdict(list)
 for ix,r in enumerate(pred,2):
  a=normalize(r.get('开始标签',''));b=normalize(r.get('结束标签',''))
  indexed[(a,b)].append((ix,r))
  if r.get('状态')=='state_exit':exits.add(a)
  if r.get('状态') in ('observed_same_network_transition','source_transition'):
   aa=clauses(a);bb=clauses(b)
   if aa and bb and len(aa)==len(bb)==1 and aa[0][0]==bb[0][0]:graph[aa[0]].append((bb[0],ix,r))
 records=[]
 for n,(start,end,desc) in enumerate(truth,1):
  a=normalize(start);b=normalize(end);exact=indexed.get((a,b))
  matched=[];status='未覆盖';reason='未找到有充分依据的候选区间'
  if exact:
   matched=[exact[0]];status='直接匹配';reason='开始及结束条件规范化后完全相同（仍需确认动作语义）'
  elif exit_equivalent(a,b,exits):
   atoms=clauses(a) or []; matched=[]
   for v,val in atoms:
    p=indexed.get((f'{v}={val}',f'NOT({v}={val})')) or indexed.get((f'{v}={val}',f'NOT ({v}={val})'))
    if p:matched.append(p[0])
   status='退出边界等价';reason='布尔状态退出条件等价，不证明动作执行完成'
  else:
   A=clauses(a);B=clauses(b)
   if A and B and len(A)==len(B)==1 and A[0][0]==B[0][0]:
    q=collections.deque([(A[0],[])]);seen={A[0]}
    while q:
     cur,path=q.popleft()
     if cur==B[0] and path:
      matched=path;break
     if len(path)>=32:continue
     for target,i,r in graph.get(cur,[]):
      if target not in seen:seen.add(target);q.append((target,path+[(i,r)]))
    if matched:status='多段候选路径';reason='同网络状态边组成路径；真实控制流与动作因果未验证'
   if not matched:
    starts=[(ix,r) for (aa,bb),z in indexed.items() if aa==a for ix,r in z]
    if starts:matched=starts[:2];status='周期待确认';reason='找到了起点，但未找到完整的终点或可靠路径'
  records.append({'人工编号':n,'人工动作描述':desc,'人工开始标签':start,'人工结束标签':end,'预测开始标签（按段）':'\n'.join(f'{j}. {r["开始标签"]}' for j,(ix,r) in enumerate(matched,1)),'预测结束标签（按段）':'\n'.join(f'{j}. {r["结束标签"]}' for j,(ix,r) in enumerate(matched,1)),'对比结果':status,'对比说明':reason,'预测原文件行号':','.join(str(ix) for ix,_ in matched),'人工原文件行号':str(n),'分段表位置':str(n)})
 counts=collections.Counter(r['对比结果'] for r in records)
 return {'metrics':{'truth_rows':len(records),'prediction_rows':len(pred),'counts':dict(counts),'supported_label_or_hypothesis':sum(counts[x] for x in ('直接匹配','退出边界等价','多段候选路径'))},'records':records,'limits':'No physical action recall proved; multi-step paths are same-network hypotheses, empty boundaries unresolved.'}

def main():
 p=argparse.ArgumentParser();p.add_argument('prediction');p.add_argument('truth');p.add_argument('-o','--out',default='benchmark_v6.json');a=p.parse_args()
 r=evaluate(csvrows(a.prediction),truth_rows(a.truth));Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(r['metrics'],ensure_ascii=False))
if __name__=='__main__':main()
