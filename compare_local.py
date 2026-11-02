"""Compare local OpenCode output against deterministic candidate baseline without conflating phases."""
import argparse,csv,json
from collections import defaultdict,Counter
from pathlib import Path
from benchmark_v7_1 import evaluate,read_csv
from benchmark_v6 import truth_rows
from benchmark_v4 import normalize

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--baseline',required=True);ap.add_argument('--local',required=True);ap.add_argument('--truth',required=True);ap.add_argument('-o','--out',required=True);a=ap.parse_args()
 b=evaluate(read_csv(a.baseline),truth_rows(a.truth));local=read_csv(a.local)
 lm=defaultdict(list)
 for i,r in enumerate(local,2):lm[normalize(r.get('开始标签','')),normalize(r.get('结束标签',''))].append(i)
 for r in b['records']:
  k=normalize(r['人工开始标签']),normalize(r['人工结束标签']);r['本地最终表直接匹配']='是' if k in lm else '否';r['本地最终表行号']=','.join(map(str,lm.get(k,[])));r['本地评估口径']='最终表精确边界（不可与宽松候选匹配率直接比较）'
 counts=Counter((r['对比结果'],r['本地最终表直接匹配']) for r in b['records'])
 summary={'baseline':b['metrics'],'local_final_rows':len(local),'local_exact_rows':sum(r['本地最终表直接匹配']=='是' for r in b['records']),'matrix':{str(k):v for k,v in counts.items()},'warning':'Baseline candidate evaluation and local OpenCode final table are DIFFERENT phases; do not claim model regression until comparing the same phase.'}
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 with (out/'diff_rows.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(b['records'][0]));w.writeheader();w.writerows(b['records'])
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
