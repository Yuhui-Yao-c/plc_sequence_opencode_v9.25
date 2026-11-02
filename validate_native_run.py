"""Independent, read-only native EQ census against a frozen prediction run.
This checks syntactic state recall, NOT physical process/GT coverage.
"""
from pathlib import Path
from collections import defaultdict
import argparse,csv,json,re
from lxml import etree
from boundary_logic import integer,IDENT,parse,comparisons,BoundarySyntaxError
from candidate_filter import glob_regex
from boundary_normalizer import write_csv

def audit(source,run,pattern=None):
    source=Path(source);run=Path(run);pat=glob_regex(pattern) if pattern else None
    census=defaultdict(set);rungs=0;blocks=0;eqblocks=0
    xsi='{http://www.w3.org/2001/XMLSchema-instance}type'
    for _,rung in etree.iterparse(str(source),events=('end',),tag='{*}Rung',huge_tree=True,resolve_entities=False,no_network=True):
        rungs+=1;values={}
        for n in rung:
            if n.get(xsi,'').split(':')[-1]=='DataSource':
                for cp in n.findall('{*}ConnectionPointOut'):
                    values[cp.get('connectionPointOutId')]=n.get('identifier','')
        for b in rung:
            if b.get(xsi,'').split(':')[-1]!='Block':continue
            blocks+=1
            if b.get('typeName','').upper().lstrip('@') not in ('=','EQ','=='):continue
            eqblocks+=1;operands=[]
            for p in b.findall('{*}InputVariables/{*}InputVariable'):
                if p.get('parameterName','').upper() in ('EN','_EN'):continue
                refs=[c.get('refConnectionPointOutId') for c in p.findall('.//{*}Connection')]
                operands.append(values.get(refs[0],'') if len(refs)==1 else '')
            if len(operands)!=2:continue
            for v,n in (operands,operands[::-1]):
                num=integer(n)
                if num is not None and re.fullmatch(IDENT,v) and (pat is None or pat.fullmatch(v)):
                    census[v].add(num)
        rung.clear()
        while rung.getprevious() is not None:del rung.getparent()[0]
    starts=defaultdict(set)
    with (run/'prediction_candidate_pairs.csv').open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            try:atoms=comparisons(parse(row.get('开始标签','')))
            except BoundarySyntaxError:continue
            for a in atoms:
                if a.kind=='eq':starts[a.args[0]].add(a.args[1])
    rows=[]
    for v,ns in sorted(census.items()):
        missing=ns-starts.get(v,set())
        rows.append({'状态变量':v,'原生EQ不同状态数':len(ns),'在候选起点找到':len(ns-missing),'缺失状态数':len(missing),
             '原生EQ状态值':';'.join(map(str,sorted(ns))),'缺失状态值':';'.join(map(str,sorted(missing))),
             '结果':'无语法状态缺口' if not missing else '需排查'})
    summary={'independent_native_rung_census':rungs,'independent_block_census':blocks,'eq_block_census':eqblocks,
        'pattern':pattern,'controller_count':len(census),'eq_states_in_scope':sum(len(s) for s in census.values()),
        'eq_states_missing_from_candidate_starts':sum(r['缺失状态数'] for r in rows),
        'scope':'Direct EQ state operands present as candidate starts. Not action coverage or control-flow proof.'}
    return summary,rows

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--run',required=True);p.add_argument('--pattern');p.add_argument('--out',required=True);a=p.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True);s,rows=audit(a.source,a.run,a.pattern)
    (out/'native_read_coverage.json').write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')
    write_csv(out/'native_state_coverage.csv',rows);print(json.dumps(s,ensure_ascii=False,indent=2))
