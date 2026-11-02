"""Conservative benchmark: exact label/step endpoints, plus reachable state graph.
Reachability is only an upper bound and does NOT prove action/event coverage.
"""
import argparse,csv,json,re,collections
from pathlib import Path
ST=re.compile(r'([\w]+(?:\.[\w]+)*\[\d+\])\s*=\s*(-?\d+)')
def records(path):
    result=[]
    with open(path,encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if '开始标签' in r and '结束标签' in r:
                if (r['开始标签'] or '').strip() and (r['结束标签'] or '').strip(): result.append(r)
    return result

def evaluate(pred,truth):
    ps=records(pred); ts=records(truth)
    beginnings={x['开始标签'].strip() for x in ps}; endings={x['结束标签'].strip() for x in ps}
    edges=collections.defaultdict(set)
    for x in ps:
        a=ST.fullmatch(x['开始标签'].strip())
        if not a:continue
        for b in ST.finditer(x['结束标签']):
            if a.group(1)==b.group(1):edges[(a.group(1),int(a.group(2)))].add(int(b.group(2)))
    report=[]
    for i,x in enumerate(ts,1):
        begin=x['开始标签'].strip();end=x['结束标签'].strip()
        a=ST.fullmatch(begin); b=ST.fullmatch(end)
        reach=False
        if a and b and a.group(1)==b.group(1):
            v=a.group(1); start=int(a.group(2));goal=int(b.group(2))
            frontier=[start];seen=set()
            while frontier:
                n=frontier.pop(0)
                if n in seen:continue
                seen.add(n)
                if n==goal:reach=True;break
                frontier.extend(edges.get((v,n),set())-seen)
        report.append({'index':i,'开始标签':begin,'结束标签':end,'exact_pair':any(begin==p['开始标签'].strip() and end==p['结束标签'].strip() for p in ps),'start_exact':begin in beginnings,'end_exact':end in endings,'step_reachable_upper_bound':reach})
    totals={k:sum(bool(x[k]) for x in report) for k in ('exact_pair','start_exact','end_exact','step_reachable_upper_bound')}
    return {'truth_count':len(ts),'pred_count':len(ps),'metrics':totals,'notes':'Step reachability may traverse unrelated branches/cycles. NOT verified action coverage.','records':report}
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('prediction');ap.add_argument('truth');ap.add_argument('-o','--output',default='coverage_report.json');a=ap.parse_args()
    result=evaluate(a.prediction,a.truth);Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False))
