"""Deterministic, model-independent comparison. Never equate state exit with feedback completion."""
import argparse,csv,json,re
from collections import Counter,defaultdict
from pathlib import Path
from benchmark_v6 import truth_rows
from benchmark_v5 import clauses,exit_equivalent
from benchmark_v4 import normalize

def read_csv(path):
 with open(path,encoding='utf-8-sig',newline='') as f:
  first=f.readline();f.seek(0)
  return list(csv.DictReader(f,delimiter='\t' if '\t' in first else ','))
def feedback_end(s):
 # Detect state equality followed by a BOOL feedback condition (not a step assignment).
 v=re.match(r'^\s*([\w.\[\]]+)\s*=\s*(?:\w+#)?(\d+)\s+AND\s+(.+)$',str(s),re.I)
 return (v.group(1),int(v.group(2)),v.group(3).strip()) if v else None

def evaluate(pred,truth):
 idx=defaultdict(list);starts=defaultdict(list);exits=set()
 for i,p in enumerate(pred,2):
  a=normalize(p.get('开始标签',''));b=normalize(p.get('结束标签',''))
  idx[a,b].append((i,p));starts[a].append((i,p))
  if p.get('状态')=='state_exit':exits.add(a)
 records=[]
 for i,(a,b,desc) in enumerate(truth,1):
  an=normalize(a);bn=normalize(b);pair=idx.get((an,bn),[]);hits=[];category='未覆盖';msg='未找到起点候选或完整边界证据'
  if pair:
   hits=pair[:3];category='直接匹配';msg='起止标签匹配；仍需核验动作因果关系'
  elif exit_equivalent(an,bn,exits):
   category='状态退出形式等价';msg='状态离开边界匹配；不代表反馈完成';atoms=clauses(an) or []
   for v,n in atoms:hits.extend(idx.get((f'{v}={n}',f'NOT({v}={n})'),[])[:1]);hits.extend(idx.get((f'{v}={n}',f'NOT ({v}={n})'),[])[:1])
  elif starts.get(an):
   hits=starts[an][:3];f=feedback_end(b)
   if f and normalize(f'{f[0]}={f[1]}')==an:
    category='起点已定位/反馈终点缺失';msg=f'人工结束依赖反馈 {f[2]}；候选只有状态迁移或退出，不能替代此反馈'
   else:category='起点已定位/结束待确认';msg='存在起点候选，但缺少完整终点证据'
  else:
   atoms=clauses(an)
   if atoms and all(starts.get(f'{v}={n}') for v,n in atoms):
    category='复合起点已定位/组合待确认';msg='组成OR分支的起点均被发现，但没有已证实的合并区间'
    for v,n in atoms:hits.extend(starts[f'{v}={n}'][:1])
  records.append({'人工编号':i,'人工动作描述':desc,'人工开始标签':a,'人工结束标签':b,'预测开始标签（按段）':'\n'.join(f'{j}. {x["开始标签"]}' for j,(lin,x) in enumerate(hits,1)),'预测结束标签（按段）':'\n'.join(f'{j}. {x["结束标签"]}' for j,(lin,x) in enumerate(hits,1)),'对比结果':category,'对比说明':msg,'预测原文件行号':','.join(str(lin) for lin,x in hits),'人工原文件行号':str(i),'分段表位置':str(i),'评估对象':'候选表（不含OpenCode整理输出）'})
 return {'metrics':{'truth_rows':len(records),'prediction_rows':len(pred),'counts':dict(Counter(r['对比结果'] for r in records))},'records':records,'limitations':'No executable motion or actual feedback completion is proven. Results must not be compared to a final_sequence.csv run as if identical phases.'}

def main():
 p=argparse.ArgumentParser();p.add_argument('prediction');p.add_argument('truth');p.add_argument('-o','--out',required=True);a=p.parse_args();r=evaluate(read_csv(a.prediction),truth_rows(a.truth));Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(r['metrics'],ensure_ascii=False))
if __name__=='__main__':main()
