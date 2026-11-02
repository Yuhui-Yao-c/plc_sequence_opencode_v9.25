"""Lossless normalization of state-exit endpoints into sourced first-exit states.

One input row -> one output row. Branches use OR in that same row. Unknown exits
retain their original label and enter review; we never guess n+10, next sorted
number, a shared distant convergence point, or an executor's physical completion.
The same code is used independently on prediction and GT during evaluation.
"""
from __future__ import annotations
import argparse,csv,json
from collections import Counter,defaultdict,deque
from pathlib import Path
from boundary_logic import parse,canonical,positive_states,exit_states,Expr,combine,BoundarySyntaxError

META=['原始开始标签','原始结束标签','边界规范化状态','退出状态集合','后继状态集合','迁移证据ID','边界转换说明','边界分支证据']
RESOLVED={'已规范化-直接后继','已规范化-区域首出口'}

def read_csv(p):
    with open(p,encoding='utf-8-sig',newline='') as f:
        first=f.readline();f.seek(0);r=csv.DictReader(f,delimiter='\t' if '\t' in first else ',')
        return list(r),r.fieldnames or ['开始标签','结束标签']

def write_csv(path,rows,fields=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fields=fields or list(dict.fromkeys(k for r in rows for k in r)) or ['开始标签','结束标签']
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def empty_graph():return {'schema':'plc.state_transitions.v1','transitions':[],'blockers':[],'warnings':['No source transition graph supplied.']}
def load_graph(path):
    if path is None:return empty_graph()
    g=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if g.get('schema')!='plc.state_transitions.v1':raise ValueError('Not a source transition graph; candidates/GT are not accepted as graph evidence')
    return g

class Normalizer:
    def __init__(self,graph):
        self.graph=graph;self.edges=defaultdict(list);self.blocks=defaultdict(list)
        for e in graph.get('transitions',[]):
            if e.get('guard_complete') is not True or e.get('evidence') not in ('plcopen_wired_guard','st_structured_guard','smc2_grid_guard','smc2_embedded_st_guard','omron_native_wired_guard','omron_native_st_guard'):
                self.blocks[e['variable'].casefold()].append({'from':e.get('from'),'reason':'unproved_edge','binding_scope':e.get('binding_scope','')});continue
            self.edges[e['variable'].casefold()].append(e)
        for b in graph.get('blockers',[]):self.blocks[b['variable'].casefold()].append(b)
    def normalize(self,row):
        # Revalidate a previous rewrite from its immutable raw labels; never trust an LLM's
        # claimed normalized flag, and do not lose transformation provenance on assemble.
        if row.get('边界规范化状态') in RESOLVED and row.get('原始结束标签'):
            row={**row,'开始标签':row.get('原始开始标签') or row.get('开始标签',''),
                 '结束标签':row['原始结束标签'],'边界规范化状态':''}
        original_start=row.get('开始标签','');original_end=row.get('结束标签','')
        result={**row,'原始开始标签':row.get('原始开始标签') or original_start,
                '原始结束标签':row.get('原始结束标签') or original_end,'边界规范化状态':'无需转换',
                '退出状态集合':'','后继状态集合':'','迁移证据ID':'','边界转换说明':'保留原边界语义','边界分支证据':''}
        if not original_start.strip() or not original_end.strip():
            result.update(边界规范化状态='待核验-边界缺失',边界转换说明='不补造起点或终点');return result
        try:a,b=parse(original_start),parse(original_end)
        except BoundarySyntaxError as e:
            result.update(边界规范化状态='待核验-表达式不支持',边界转换说明=str(e));return result
        region=exit_states(b)
        if not region:
            if positive_states(b):result['边界转换说明']='终点已经是明确状态；未据此证明该预测迁移正确'
            else:result['边界转换说明']='反馈/握手或其他非纯状态退出终点保留；不强行改成状态号'
            return result
        entry=positive_states(a)
        if not entry or entry[0].casefold()!=region[0].casefold() or not entry[1].issubset(region[1]):
            result.update(边界规范化状态='待核验-起点与退出集合不一致',边界转换说明='仅处理同控制器、起点状态包含于退出集合的标签；不删除额外起点条件');return result
        variable,region_set=region;starts=entry[1];v=variable.casefold()
        result['退出状态集合']=';'.join(map(str,sorted(region_set)))
        edges=self.edges.get(v,[]);blocks=self.blocks.get(v,[])
        scopes={e.get('binding_scope','') for e in edges}|{e.get('binding_scope','') for e in blocks}
        context=row.get('证据POU','') or row.get('scope','')
        if len(scopes)>1:
            matched={s for s in scopes if s and s in context.split(';') or s and context.startswith(s+'::')}
            if len(matched)!=1:
                result.update(边界规范化状态='待核验-变量作用域歧义',边界转换说明='同名状态变量属于不同POU；缺少唯一作用域，不能串接');return result
            scope=next(iter(matched));edges=[e for e in edges if e.get('binding_scope','')==scope];blocks=[e for e in blocks if e.get('binding_scope','')==scope]
        by_state=defaultdict(list)
        for e in edges:by_state[int(e['from'])].append(e)
        q=deque(sorted(starts));seen=set();frontier=set();used={};fail=[];internal=False;branches=[]
        while q:
            s=q.popleft()
            if s in seen:continue
            seen.add(s)
            bad=[x for x in blocks if x.get('from') is None or int(x['from'])==s]
            if bad:fail.extend(f'{s}: {x.get("reason","unknown writer")}' for x in bad)
            outs=by_state.get(s,[])
            if not outs:fail.append(f'{s}: no complete sourced outgoing transition');continue
            for e in outs:
                used[e['id']]=e;t=int(e['to'])
                branches.append({'from':s,'to':t,'guard':e['guard'],'transition_id':e['id'],'scope':e.get('scope','')})
                if t in region_set:
                    internal=True
                    if t not in seen:q.append(t)
                else:frontier.add(t)
        result['迁移证据ID']=';'.join(sorted(used))
        result['后继状态集合']=';'.join(map(str,sorted(frontier)))
        result['边界分支证据']=json.dumps(branches,ensure_ascii=False,separators=(',',':'))
        if fail or not frontier:
            result.update(边界规范化状态='待核验-后继证据不完整',
                边界转换说明='；'.join(dict.fromkeys(fail))[:3000] if fail else '仅有集合内部回环，没有可证明出口；保留原终点')
            return result
        target=combine('or',*(Expr('eq',(entry[0],n)) for n in sorted(frontier)))
        result['结束标签']=target.text()
        result['边界规范化状态']='已规范化-区域首出口' if internal else '已规范化-直接后继'
        result['边界转换说明']=('从起点沿源码支持的迁移，取第一次离开退出集合的状态；不跨过出口找更远汇合点。'
            '全部分支同一行OR表达，含复位/异常出口时也保留。局部状态模型转换，不证明采样时间戳、路径活性或机械完成。')
        return result

def normalize_rows(rows,graph):
    n=Normalizer(graph);out=[n.normalize(r) for r in rows]
    return out,{'input_rows':len(rows),'output_rows':len(out),'row_count_preserved':len(rows)==len(out),
        'converted_rows':sum(r['边界规范化状态'] in RESOLVED for r in out),
        'review_rows':sum(r['边界规范化状态'].startswith('待核验') for r in out),
        'categories':dict(Counter(r['边界规范化状态'] for r in out)),
        'source_sha256':graph.get('source_sha256'),'limits':graph.get('limits',graph.get('warnings',[]))}

def normalize_file(input_path,output_path,graph,review_path=None):
    rows,fields=read_csv(input_path);rr,stats=normalize_rows(rows,graph)
    write_csv(output_path,rr,list(dict.fromkeys(fields+META)))
    if review_path:write_csv(review_path,[r for r in rr if r['边界规范化状态'].startswith('待核验')],list(dict.fromkeys(fields+META)))
    return stats
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('--out',required=True)
    g=p.add_mutually_exclusive_group(required=True);g.add_argument('--xml');g.add_argument('--graph')
    a=p.parse_args()
    if Path(a.input).resolve()==Path(a.out).resolve():p.error('Use a separate output; never overwrite original predictions')
    if a.xml:
        from successor_graph import write_graph
        graph=write_graph(a.xml,Path(a.out).parent/'source_evidence')
    else:graph=load_graph(a.graph)
    print(json.dumps(normalize_file(a.input,a.out,graph),ensure_ascii=False,indent=2))
