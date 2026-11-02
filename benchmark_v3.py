#!/usr/bin/env python3
"""Transparent benchmark. Separate exact pairs, observed-state anchors, speculative adjacency paths.
Never count adjacency hypotheses as verified action coverage.
"""
import argparse,csv,re,json,collections
from pathlib import Path
STATE=re.compile(r'([\w.]+(?:\[\d+\])?)\s*=\s*(-?\d+)')
def md(path):
    rows=[]
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if not line.lstrip().startswith('|'):continue
        cells=[x.strip().replace('\xa0',' ') for x in line.strip().strip('|').split('|')]
        if len(cells)>=3 and '开始标签' not in cells and '结束标签' not in cells and cells[-2] and cells[-1] and not set(cells[-1])<=set('- :'):
            rows.append((cells[-2],cells[-1],cells[0]))
    return rows
def csvrows(path):
    with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def norm(t):return re.sub(r'\s+','',t or '').upper()
def assess(pred,truth):
    pairs={(norm(r['开始标签']),norm(r['结束标签'])) for r in pred}
    states=collections.defaultdict(set)
    for r in pred:
        for label in (r['开始标签'],r['结束标签']):
            for var,n in STATE.findall(label):states[var].add(int(n))
    allrows=[]
    for i,(a,b,description) in enumerate(truth,1):
        starts=STATE.findall(a);ends=STATE.findall(b)
        a_anchor=all(int(n) in states.get(v,set()) for v,n in starts) if starts else False
        b_anchor=all(int(n) in states.get(v,set()) for v,n in ends) if ends else False
        same=bool(starts and ends and starts[0][0]==ends[0][0])
        exact=(norm(a),norm(b)) in pairs
        status=('exact' if exact else 'both_state_anchors_only' if a_anchor and b_anchor else 'partial_anchors' if a_anchor or b_anchor else 'no_state_anchors')
        allrows.append({'row':i,'description':description,'start':a,'end':b,'exact':exact,'start_state_anchor':a_anchor,'end_state_anchor':b_anchor,'same_controller':same,'status':status})
    metrics={k:sum(x[k] for x in allrows) for k in ('exact','start_state_anchor','end_state_anchor')}
    metrics['both_state_anchors']=sum(x['start_state_anchor'] and x['end_state_anchor'] for x in allrows)
    metrics['ground_truth_rows']=len(allrows);metrics['pred_rows']=len(pred)
    return {'metrics':metrics,'notes':'State anchors are not verified transitions; exact matches can also be coincidental. Source-grounded action coverage is NOT established by this test.','records':allrows}
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('prediction');a.add_argument('truth');a.add_argument('-o','--out',default='benchmark.json');args=a.parse_args()
    truth=md(args.truth) if args.truth.endswith(('.md','.txt')) else [(r['开始标签'],r['结束标签'],r.get('动作描述','')) for r in csvrows(args.truth) if r.get('开始标签') and r.get('结束标签')]
    r=assess(csvrows(args.prediction),truth);Path(args.out).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(r['metrics'],ensure_ascii=False))
