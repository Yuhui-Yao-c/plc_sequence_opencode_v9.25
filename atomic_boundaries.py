"""Source-only atomic boundary view. Never converts a formatting wish into a proof.

Every exported endpoint is ONE state equality or a BOOL signal. No compound is stripped. Full input records remain in the
lossless candidate pool. OR alternatives are expanded with branch/region lineage,
not a Cartesian product. Unsupported AND/feedback semantics are reviewed, not
stripped. Local first-exit paths are distinct from whole-writer equivalence.
"""
from __future__ import annotations
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
import argparse
import hashlib
import json
from typing import Any
from boundary_logic import (parse, canonical, positive_states, exit_states, Expr,
                            BoundarySyntaxError, compatible)
from feedback_successor import FeedbackResolver
from boundary_normalizer import (Normalizer, RESOLVED, empty_graph, load_graph,
                                 read_csv, write_csv)

PROVEN = {'plcopen_wired_guard','st_structured_guard','smc2_grid_guard',
          'smc2_embedded_st_guard','omron_native_wired_guard','omron_native_st_guard'}
ATOMIC_FIELDS = ['原子对ID','构成要素','动作','动作描述','开始标签','结束标签',
                 '证据等级','全局退出等价','计时使用方式','来源候选ID','计时组ID',
                 '迁移证据ID','证据POU','关联条件文件','说明',
                 '过程类型','统计对象','起点含义','终点含义','工艺依据','边界依据']

def stable_id(prefix: str, value: Any) -> str:
    b=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    return prefix+hashlib.sha256(b).hexdigest()[:16]

def atomic(text: str) -> Expr | None:
    try:
        e=parse(text)
        return e if e.kind in ('eq','signal') else None
    except (BoundarySyntaxError, TypeError):
        return None

def pair_key(start: str, end: str) -> tuple[str,str]:
    return canonical(start),canonical(end)

def state_text(v: str,n: int) -> str:
    return Expr('eq',(v,n)).text()

@dataclass
class Projection:
    rows: list[dict] = field(default_factory=list)
    status: str = '待核验'
    reason: str = ''
    strict_equivalence: bool = False
    region: list[int] = field(default_factory=list)
    entries: list[int] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    internal_entry_edges: bool = False
    # Different branch conditions are ALWAYS retained here, outside the endpoints.
    paths: list[dict] = field(default_factory=list)

class AtomicProjector:
    """Works on source graph only; has no GT, sample answers or controller-name rules."""
    def __init__(self, graph: dict | None = None, max_states: int = 256,
                 max_pairs: int = 256):
        self.graph=graph or empty_graph()
        if self.graph.get('schema')!='plc.state_transitions.v1':
            raise ValueError('需要源码迁移图，不能用候选/GT代替证据')
        self.max_states=max_states;self.max_pairs=max_pairs
        self.normalizer=Normalizer(self.graph)
        self.feedback_resolver=FeedbackResolver(self.graph)
        self.edges=defaultdict(list);self.blocks=defaultdict(list)
        for e in self.graph.get('transitions',[]):
            v=e.get('variable','').casefold()
            if not v:continue
            if e.get('guard_complete') is True and e.get('evidence') in PROVEN:
                self.edges[v].append(e)
            else:
                self.blocks[v].append({'from':e.get('from'),'binding_scope':e.get('binding_scope',''),
                                      'reason':'unproved_transition','scope':e.get('scope','')})
        for b in self.graph.get('blockers',[]):
            self.blocks[b.get('variable','').casefold()].append(b)
        for edges in self.edges.values():
            edges.sort(key=lambda x:(x.get('binding_scope',''),int(x['from']),int(x['to']),x.get('id','')))

    def _scope(self,v: str,row: dict) -> tuple[list, list, str]:
        edges=self.edges.get(v.casefold(),[]);blocks=self.blocks.get(v.casefold(),[])
        scopes={e.get('binding_scope','') for e in edges+blocks}
        if len(scopes)<=1:return edges,blocks,''
        context=row.get('证据POU','') or row.get('scope','')
        explicit=row.get('binding_scope')
        if explicit is not None and explicit in scopes:found={explicit}
        else:
            found={s for s in scopes if s and any(x==s or x.startswith(s+'::') for x in context.split(';'))}
        if len(found)!=1:return [],[], '同名变量作用域不唯一；不得跨POU连边'
        s=next(iter(found))
        return ([e for e in edges if e.get('binding_scope','')==s],
                [b for b in blocks if b.get('binding_scope','')==s], '')

    def _first_exit(self,v: str,start: int,region: set[int],edges: list,
                    blocks: list) -> tuple[dict[int,list], list[str], bool]:
        by=defaultdict(list)
        for e in edges:by[int(e['from'])].append(e)
        q=deque([start]);seen=set();exits=defaultdict(list);visited_edges={};issues=[];internal=False
        while q:
            s=q.popleft()
            if s in seen:continue
            seen.add(s)
            if len(seen)>self.max_states:
                issues.append('状态集合搜索超过上限；不截断后冒充完整');break
            outs=by.get(s,[])
            if not outs:issues.append(f'{s}: 未找到完整局部门控后继')
            for b in blocks:
                if b.get('from') is None or int(b['from'])==s:
                    issues.append(f'{s}: {b.get("reason","未知写入")}')
            for e in outs:
                dest=int(e['to']);visited_edges[e['id']]=e
                if dest in region:
                    internal=True
                    if dest not in seen:q.append(dest)
                else:
                    exits[dest].append(e)
        # A reachable exit is a candidate, not a claim every path eventually exits.
        # Keep full per-hop edges, NOT AND(all guards across different scans).
        trace=list(visited_edges.values());traces={}
        for target in sorted(exits):
            # Reverse-reachable subgraph for this exit only. Do not present the
            # other branch's guard as a requirement of this branch.
            good={target};changed=True
            while changed:
                changed=False
                for edge in trace:
                    if int(edge['to']) in good and int(edge['from']) not in good:
                        good.add(int(edge['from']));changed=True
            traces[target]=[e for e in trace if int(e['from']) in good and int(e['to']) in good
                            and (int(e['to']) in region or int(e['to'])==target)]
        return traces,list(dict.fromkeys(issues)),internal

    def project(self,row: dict) -> Projection:
        s=str(row.get('开始标签','') or '').strip();t=str(row.get('结束标签','') or '').strip()
        if not s or not t:return Projection(reason='边界缺失；原候选保留，不造终点')
        try:a,b=parse(s),parse(t)
        except BoundarySyntaxError as ex:return Projection(status='写法待核验',reason=str(ex))
        # Signals are primitive endpoints too. Preserve original identity; a BOOL
        # must not be replaced by a nearby state just to satisfy a formatting rule.
        if a.kind in ('eq','signal') and b.kind in ('eq','signal') and 'signal' in (a.kind,b.kind):
            if a.key()==b.key():
                return Projection(status='周期待核验',reason='同一信号起止需独立的循环/边沿定义')
            matches=[r for r in self.graph.get('transport_relations',[])
                     if pair_key(r.get('start',''),r.get('end',''))==pair_key(s,t)]
            return Projection(rows=[{'开始标签':a.text(),'结束标签':b.text(),
                '证据等级':'源码跨功能块关联-待运行核验' if matches else '原始单对-路径未证明',
                '全局退出等价':'不适用','计时使用方式':'触发信号上升沿启动；同一循环首次终点结束；异常取消，不重复启动',
                '迁移证据ID':';'.join(r['id'] for r in matches)}],
                status='信号原子标签保留',reason='非复合不限定步号；原式保留非物理完成/时间等价证明',
                strict_equivalence=True,paths=[{'start_label':s,'end_label':t,'transport_relations':matches}])
        # Already atomic pairs do not need a graph and are NOT magically validated.
        if a.kind=='eq' and b.kind=='eq':
            if a.key()==b.key():
                return Projection(status='周期待核验',reason='同起止状态需定义离开再进入；不得作为零时长或任意下一步')
            es,bs,scope_issue=self._scope(a.args[0],row)
            if scope_issue:return Projection(status='作用域待核验',reason=scope_issue)
            matching=[e for e in es if e['variable'].casefold()==b.args[0].casefold()
                      and int(e['from'])==a.args[1] and int(e['to'])==b.args[1]]
            return Projection(rows=[{'开始标签':a.text(),'结束标签':b.text(),
                       '证据等级':'源码局部迁移' if matching else '原始单对-路径未证明',
                       '全局退出等价':'不适用','计时使用方式':'按本行起止配对；检查分支门控及循环实例',
                       '迁移证据ID':';'.join(e['id'] for e in matching)}],
                       status='原子标签保留',reason='原始起止均为单状态；保留不是动作/时序正确性证明',
                       strict_equivalence=True,paths=[{'start':a.args[1],'end':b.args[1],
                       'edges':matching}])
        feedback = self.feedback_resolver.resolve(row)
        if feedback.applicable:
            return Projection(rows=feedback.rows, status=feedback.status,
                reason=feedback.reason, strict_equivalence=False,
                entries=[a.args[1]], region=[a.args[1]],
                issues=feedback.issues, paths=feedback.paths)
        starts=positive_states(a)
        if not starts:
            return Projection(status='复合起点待核验',reason=(
                'AND是同时满足条件，不是任选一支；不得删除同步/反馈条件或把整个区间平移到后继。'
                '需有独立的事件别名/时序证据才能替换，当前不自动完成。'))
        region=exit_states(b);ends=positive_states(b)
        if region:
            if starts[0].casefold()!=region[0].casefold() or not starts[1].issubset(region[1]):
                return Projection(status='控制器或集合待核验',reason='起点与退出集合不一致；不修正GT/源码下标')
            mode='exit';allowed=set(region[1])
        elif ends and starts[0].casefold()==ends[0].casefold():
            # Explicit compound ends are never cartesian-expanded. Only first exits
            # of the start set that belong to the given endpoint set are admissible.
            mode='positive';allowed=set(starts[1])
            if starts[1] & ends[1]:
                return Projection(status='周期待核验',reason='起点与终点集合重叠，需指定首次/再次进入，不能默认零时长')
        else:
            return Projection(status='非状态终点待核验',reason='不把反馈/握手或跨控制器复合终点强行改成步号')
        v=starts[0];es,bs,issue=self._scope(v,row)
        if issue:return Projection(status='作用域待核验',reason=issue)
        out=Projection(region=sorted(allowed),entries=sorted(starts[1]))
        strict=self.normalizer.normalize(row) if mode=='exit' else None
        for start in sorted(starts[1]):
            exits,issues,internal=self._first_exit(v,start,allowed,es,bs)
            out.issues.extend(issues)
            # An internal transition into another OR start would retrigger simple
            # per-row timers: disclose it, never claim a lossless disjoint split.
            if len(starts[1])>1 and any(int(e['to']) in starts[1] and int(e['from'])!=int(e['to']) for trace in exits.values() for e in trace):out.internal_entry_edges=True
            for end,trace in exits.items():
                if mode=='positive' and end not in ends[1]:
                    out.issues.append(f'{start}: 源码首出口{end}不在给定终点集合，不能跳过它');continue
                out.rows.append({'开始标签':state_text(v,start),'结束标签':state_text(v,end),
                    '证据等级':'源码集合首出口-局部条件',
                    '全局退出等价':'是' if strict and strict['边界规范化状态'] in RESOLVED else '否/未证明',
                    '计时使用方式':'同组共用一次起点；按实际出口选择分支，其他分支取消；异常需单独结束',
                    '迁移证据ID':';'.join(sorted({e['id'] for e in trace}))})
                out.paths.append({'start':start,'end':end,'edges':trace})
                if len(out.rows)>self.max_pairs:
                    return Projection(status='展开上限待核验',reason='分支过多；不输出截断集合',
                                      region=sorted(allowed),entries=sorted(starts[1]))
        out.issues=list(dict.fromkeys(out.issues))
        if out.internal_entry_edges:
            # Do not emit misleading two-column rows for an internally connected
            # OR start. A separate collector region-start latch would be required.
            return Projection(status='OR集合入口待核验',reason=(
                'OR起点之间存在内部迁移；按单状态触发会重复开始计时。需集合首次进入检测，'
                '不能把内部切步当成新动作。已保留路径诊断，未伪造原子等价。'),
                region=out.region,entries=out.entries,issues=out.issues,
                internal_entry_edges=True,paths=out.paths)
        if not out.rows:
            out.status='后继缺失待核验';out.reason='未找到有完整局部门控的首出口；不按数值排序补造'
            return out
        # Every disjunct must have a known exit before claiming complete branch view.
        present={atomic(r['开始标签']).args[1] for r in out.rows}
        missing=starts[1]-present
        if missing:out.issues.append('未覆盖OR入口: '+','.join(map(str,sorted(missing))))
        out.strict_equivalence=bool(mode=='exit' and strict and strict['边界规范化状态'] in RESOLVED and not missing and not out.issues)
        out.status='集合退出已证明' if out.strict_equivalence else '局部后继候选-需核验'
        out.reason=('所有已证明路径在第一次离开指定集合时停止；不是编号相邻或更远汇合点。'
            '分支条件在条件文件中逐跳保留。局部门控不等于完整写入覆盖、运行可达性或CT时间戳等价。')
        for r in out.rows:
            if out.strict_equivalence:r['证据等级']='源码集合首出口-全写入检查通过'
            r['全局退出等价']='是' if out.strict_equivalence else '否/未证明'
        return out


def export_atomic(rows: list[dict],graph: dict,output_dir: str | Path,
                  prefix: str='prediction') -> dict:
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    projector=AtomicProjector(graph);pairs={};lineage=[];review=[];groups=[];status=Counter()
    condition_name=f'{prefix}_atomic_conditions.json'
    for i,row in enumerate(rows,1):
        cid=row.get('候选ID') or row.get('原子对ID') or f'ROW{i:07d}'
        # Project only actual endpoint cells, never substitute a claimed original/GT label.
        pr=projector.project(row);status[pr.status]+=1
        gid=stable_id('G',[i,cid,row.get('开始标签'),row.get('结束标签')])
        ids=[]
        for branch in pr.rows:
            a,b=branch['开始标签'],branch['结束标签']
            if atomic(a) is None or atomic(b) is None:raise AssertionError('原子标签校验失败')
            # Include binding scope to avoid accidental name collisions; evaluator
            # will additionally require unique scope when its GT is ambiguous.
            scope=row.get('binding_scope','')
            semantic_key=tuple(str(row.get(k,'')) for k in ('构成要素','动作','动作描述','过程类型','统计对象','起点含义','终点含义')) if prefix in ('process','sequence') else ()
            key=(pair_key(a,b),scope,semantic_key);aid=stable_id('A',key);ids.append(aid)
            if key not in pairs:
                pairs[key]={'原子对ID':aid,'构成要素':row.get('构成要素',''),
                    '动作':row.get('动作',''),'动作描述':row.get('动作描述',''),**branch,
                    '来源候选ID':set(),'计时组ID':set(),'证据POU':set(),
                    '关联条件文件':condition_name,'说明':pr.reason,'_grades':set(),'_proof':set(),'_ids':set(),
                    **{k:row.get(k,'') for k in ('过程类型','统计对象','起点含义','终点含义','工艺依据','边界依据')}}
            p=pairs[key];p['来源候选ID'].add(str(cid));p['计时组ID'].add(gid)
            p['证据POU'].update(x for x in str(row.get('证据POU','')).split(';') if x)
            p['_grades'].add(branch['证据等级']);p['_proof'].add(branch['全局退出等价'])
            p['_ids'].update(x for x in branch.get('迁移证据ID','').split(';') if x)
        lineage.append({'原候选序号':i,'候选ID':cid,'原始开始标签':row.get('开始标签',''),
            '原始结束标签':row.get('结束标签',''),'处理结果':pr.status,'原子对ID':';'.join(ids),
            '计时组ID':gid,'输出分支数':len(ids),'全局退出等价':'是' if pr.strict_equivalence else '否/未证明',
            '说明':pr.reason,'阻断项数':len(pr.issues)})
        if not pr.rows or pr.issues:
            review.append({**lineage[-1],'阻断项':'；'.join(pr.issues)[:8000]})
        groups.append({'group_id':gid,'parent_id':cid,'parent_row':i,
            'original_start':row.get('开始标签',''),'original_end':row.get('结束标签',''),
            'atomic_pair_ids':ids,'status':pr.status,'strict_equivalence':pr.strict_equivalence,
            'excluded_region':pr.region,'entry_states':pr.entries,
            'internal_entry_edges':pr.internal_entry_edges,'paths':pr.paths,'issues':pr.issues,
            'reason':pr.reason})
    feedback_rows=[]
    for group in groups:
        if not group['status'].startswith('反馈'):
            continue
        paths=group['paths'] or [{}]
        for path in paths:
            relations=path.get('feedback_relations', [])
            feedback_rows.append({'候选ID':group['parent_id'],
                '原始开始标签':group['original_start'],'原始结束标签':group['original_end'],
                '处理结果':group['status'],'逻辑后继值':path.get('end',''),
                '迁移证据ID':';'.join(e.get('id','') for e in path.get('edges',[])),
                '完整门控':' OR '.join('('+e.get('guard','')+')' for e in path.get('edges',[])),
                '附加门控':' OR '.join('('+r['extra_guard']+')' for r in relations),
                '严格等价':'未证明','说明':group['reason'],'阻断项':'；'.join(group['issues'])})
    write_csv(out/f'{prefix}_feedback_successor_audit.csv',feedback_rows,
              list(feedback_rows[0]) if feedback_rows else ['候选ID','原始开始标签','原始结束标签','处理结果','说明'])
    result=[]
    for p in pairs.values():
        for k in ('来源候选ID','计时组ID','证据POU'):p[k]=';'.join(sorted(p[k]))
        p['证据等级']=';'.join(sorted(p.pop('_grades')))
        proofs=p.pop('_proof');p['全局退出等价']='存在已证明来源' if '是' in proofs else '否/未证明' if '否/未证明' in proofs else '不适用'
        p['迁移证据ID']=';'.join(sorted(p.pop('_ids')));result.append(p)
    result.sort(key=lambda r:(canonical(r['开始标签']),canonical(r['结束标签']),r['原子对ID']))
    output=out/f'{prefix}_atomic_pairs.csv'
    write_csv(output,result,ATOMIC_FIELDS)
    write_csv(out/f'{prefix}_atomic_lineage.csv',lineage,
              list(lineage[0]) if lineage else ['原候选序号','候选ID','处理结果','原子对ID'])
    write_csv(out/f'{prefix}_atomic_review.csv',review,
              list(review[0]) if review else ['候选ID','处理结果','说明'])
    evidence={'schema':'plc.atomic_boundaries.v1','source_sha256':graph.get('source_sha256'),
              'input_rows':len(rows),'prediction_uses_gt':False,'groups':groups}
    (out/condition_name).write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    stats={'version':'9.13','input_complete_pairs':len(rows),'unique_atomic_pairs':len(result),
           'rows_with_atomic_output':sum(bool(r['原子对ID']) for r in lineage),
           'rows_without_atomic_output':sum(not r['原子对ID'] for r in lineage),
           'feedback_successor_audit_rows':len(feedback_rows),'review_rows_including_conditional':len(review),'categories':dict(status),
           'every_input_accounted':len(lineage)==len(rows),
           'all_exported_endpoints_atomic':all(atomic(r['开始标签']) and atomic(r['结束标签']) for r in result),
           'source_sha256':graph.get('source_sha256'),'no_gt_access':True,
           'main_output':output.name,'raw_pool_not_mutated':True,
           'note':'原子表是来源可追溯视图；不同分支、动态/复位及未解析写入的证据不能被格式转换掩盖。'}
    (out/f'{prefix}_atomic_summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
    return stats

if __name__=='__main__':
    p=argparse.ArgumentParser(description='源码支持的非复合标签视图；无GT输入')
    p.add_argument('--prediction',required=True);p.add_argument('--graph',required=True)
    p.add_argument('--out',required=True);p.add_argument('--prefix',default='prediction')
    a=p.parse_args()
    if not a.prefix.replace('_','').isalnum():p.error('prefix只允许字母数字下划线')
    if Path(a.prediction).resolve()==(Path(a.out)/f'{a.prefix}_atomic_pairs.csv').resolve():
        p.error('不得覆盖输入预测，请用独立输出目录')
    rows,_=read_csv(a.prediction);g=load_graph(a.graph)
    print(json.dumps(export_atomic(rows,g,a.out,a.prefix),ensure_ascii=False,indent=2))
