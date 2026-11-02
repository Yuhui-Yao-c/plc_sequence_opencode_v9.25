"""Source-only action + signal/data handoff composition.

No GT input, no controller names/indices/step constants. The original candidate
pool is never deleted. A composed interval is a NEW timing interval, NOT a
Boolean alias for either constituent. Local path evidence != runtime proof.
"""
from __future__ import annotations
from collections import defaultdict, Counter
from pathlib import Path
import argparse, csv, hashlib, json, re
from boundary_logic import parse, Expr, canonical, BoundarySyntaxError
from boundary_normalizer import write_csv

MOTION_CALL = re.compile(r'axis.*(?:move|jog)|mc_(?:move|jog)|(?:move|jog).*axis|cycontrol|cylinder|robot.*(?:move|run)|vacuum|servo.*(?:move|jog)',re.I)
MOTION_FIELD = re.compile(r'(?:^|\.)(?:JogCW|JogCCW|AbsEx|RelEx|Anastole|StrOut|MoveAbsolute|MoveRelative|MoveVelocity|CmdMove|VacuumOn)(?:\[.*\])?$',re.I)
UNSAFE_DATA_FIELD = re.compile(r'(?:^|\.)(?:Execute|Enable|Start|Run|Reset|Stop|JogCW|JogCCW|Abs|Rel|AbsEx|RelEx|Output|Q|Trigger|Motion|Cmd)(?:\[.*\])?$',re.I)
WAIT = re.compile(r'等待|\bwait(?:ing)?\b|\bawait\b',re.I)
TIMERS={'TON','TOF','TP'}


def key_pair(r):return canonical(r.get('开始标签','')),canonical(r.get('结束标签',''))
def atom(s):
    try:
        x=parse(s)
        return x if x.kind=='eq' else None
    except (BoundarySyntaxError,TypeError):return None

def _id(prefix,data):
    return prefix+hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16]

def _key(e,value):return e.get('scope',''),e.get('binding_scope',''),e['variable'].casefold(),int(value)
def _edge_network(e):return str(e.get('body','')).split(';')[0]
def _same_controller(target,v):return str(target).casefold()==v.casefold()

def motion_command(c):
    cmd=c.get('command','');args=c.get('arguments','')
    return bool(MOTION_CALL.search(cmd) or (cmd.startswith('coil_') and MOTION_FIELD.search(args)))


def discover(graph:dict,max_hops:int=16)->dict:
    """Classify source slices and compose unique local paths with positive effects.

    This first adapter consumes native_inline_slices. Missing semantic evidence
    never turns an arbitrary no-command state into a transfer stage.
    """
    if max_hops<1:raise ValueError('max_hops must be positive')
    es=[e for e in graph.get('transitions',[]) if e.get('from') is not None and e.get('to') is not None and int(e['from'])!=int(e['to'])]
    outgoing=defaultdict(list);incoming=defaultdict(list)
    for e in es:
        outgoing[_key(e,e['from'])].append(e);incoming[_key(e,e['to'])].append(e)
    cmds=defaultdict(list)
    for c in graph.get('native_commands',[]):
        a=atom(c.get('start',''))
        if a:cmds[(c['scope'],a.args[0].casefold(),a.args[1])].append(c)
    state_vars={x.get('variable','').casefold() for x in graph.get('state_observations',[]) if x.get('kind') in ('comparison','st_comparison')}
    tail={};reviews=[]
    for sl in graph.get('native_inline_slices',[]):
        seeds=[a for a in (atom(x) for x in sl.get('state_seeds',[])) if a]
        for seed in seeds:
            v,n=seed.args;reasons=[]
            source_edges=[e for e in es if e['scope']==sl['scope'] and e['variable'].casefold()==v.casefold() and int(e['from'])==n and _edge_network(e)==sl['network']]
            if not source_edges:continue
            bindings={e.get('binding_scope','') for e in source_edges}
            if len(bindings)!=1:continue
            k=(sl['scope'],next(iter(bindings)),v.casefold(),n)
            state_writes=[a for a in sl.get('assignments',[]) if _same_controller(a['target'],v)]
            data=[a for a in sl.get('assignments',[]) if not _same_controller(a['target'],v)]
            all_commands=cmds.get((sl['scope'],v.casefold(),n),[])
            nonhelper=[c for c in all_commands if c['command'].upper() not in TIMERS]
            if not sl.get('flow_complete'):reasons.append('unsupported_inline_control_flow')
            if sl.get('skipped_statements'):reasons.append('skipped_st_call_may_write_state')
            if sl.get('external_issue'):reasons.append('unresolved_external_enable')
            if len(state_writes)!=1 or state_writes[0].get('integer') is None or state_writes[0].get('guard')!='TRUE':
                reasons.append('tail_needs_one_unconditional_literal_state_assignment')
            if not data:reasons.append('no_positive_signal_or_data_effect')
            if any(a['target'].casefold() in state_vars for a in data):reasons.append('writes_another_state_controller')
            if any(UNSAFE_DATA_FIELD.search(a['target']) for a in data):reasons.append('possible_actuator_or_control_assignment')
            if any(motion_command(c) for c in nonhelper):reasons.append('new_physical_action_in_tail')
            elif nonhelper:reasons.append('unclassified_call_or_coil_in_tail')
            if WAIT.search(sl.get('comment','')):reasons.append('independent_wait_semantics_need_review')
            # The same state must not execute an unclassified inline body elsewhere.
            others=[o for o in graph.get('native_inline_slices',[]) if o is not sl and o.get('scope')==sl['scope'] and seed.text() in o.get('state_seeds',[])]
            if others:reasons.append('multiple_inline_bodies_for_state')
            if len({e['to'] for e in outgoing[k]})!=1:reasons.append('branching_tail_exit')
            if len({e['from'] for e in incoming[k]})!=1:reasons.append('ambiguous_or_missing_predecessor')
            base={'scope':sl['scope'],'variable':v,'state':n,'network':sl['network'],
                'source_line':sl['source_line'],'comment':sl.get('comment',''),'reasons':reasons}
            if reasons:reviews.append(base);continue
            tail[k]={'slice':sl,'data_effects':data,'edges':source_edges,'key':k}
    proposals=[];used=set()
    for k,outs in sorted(outgoing.items()):
        scope,bs,v,n=k;action_cmds=[c for c in cmds.get((scope,v,n),[]) if motion_command(c)]
        if not action_cmds or len({e['to'] for e in outs})!=1:continue
        first=sorted(outs,key=lambda e:e['id'])[0]
        if _key(first,first['to']) not in tail:continue
        chain=[first];tail_slices=[];seen={n};cur=first['to'];issues=[]
        while (scope,bs,v,cur) in tail:
            if cur in seen:issues.append('cycle_in_signal_tail');break
            if len(tail_slices)>=max_hops:issues.append('signal_chain_budget_exceeded');break
            seen.add(cur);t=tail[(scope,bs,v,cur)];tail_slices.append(t)
            ee=sorted(outgoing[(scope,bs,v,cur)],key=lambda e:e['id'])[0]
            if {e['from'] for e in incoming[(scope,bs,v,cur)]}!={chain[-1]['from']}:issues.append('shared_signal_tail');break
            chain.append(ee);cur=ee['to']
        if cur in seen:issues.append('cycle_returns_to_action_or_tail')
        if issues:
            reviews.append({'scope':scope,'variable':first['variable'],'state':n,'reasons':issues});continue
        # No arbitrary move past the first non-tail state (which may start a new action).
        mid=[e['to'] for e in chain[:-1]];original_v=first['variable']
        start=f'{original_v}={n}';end=f'{original_v}={cur}'
        if start==end:continue
        # Keep all known same-edge parallel guards; this is a sequence, not a
        # simultaneous AND of mutually exclusive states.
        paths=[]
        for e in chain:
            alternatives=[x for x in outgoing[_key(e,e['from'])] if x['to']==e['to']]
            paths.append({'from':e['from'],'to':e['to'],'alternatives':alternatives})
        pid=_id('M',[graph.get('source_sha256'),scope,bs,original_v,n,cur,mid])
        row={'构成要素':scope,'动作':'动作及后续信号传递','动作描述':action_cmds[0].get('comment','')+'；并入：'+'；'.join(t['slice'].get('comment','') for t in tail_slices),
            '开始标签':start,'结束标签':end,'状态':'action_signal_tail_composed','证据POU':scope,
            '证据节点':pid,'原始条件':'有序路径：'+' → '.join(map(str,[n]+mid+[cur]))+'；逐跳门控见signal_merge_lineage.json',
            '控制器':original_v,'分支数':0}
        proposals.append({'merge_id':pid,'row':row,'scope':scope,'binding_scope':bs,
            'variable':original_v,'action_start':n,'tail_states':mid,'end_state':cur,
            'constituent_pairs':[{'开始标签':f'{original_v}={e["from"]}','结束标签':f'{original_v}={e["to"]}'} for e in chain],
            'ordered_paths':paths,'action_commands':action_cmds,
            'tail_effects':[t['slice'] for t in tail_slices],
            'timing_semantics':'new_action_plus_signal_tail_interval_not_boundary_equivalence',
            'proof_level':'source_structured_path_and_role_heuristic_needs_process_review',
            'no_gt_access':True,
            'limitations':['Motion classification is a generic command/operand heuristic; not physical completion proof.',
                'Whole-program reset/dynamic writes and scan timing remain unverified.',
                'Internal stabilization timers and processing duration are included, not erased.']})
        used.update(t['key'] for t in tail_slices)
    for k,t in tail.items():
        if k not in used:reviews.append({'scope':k[0],'variable':t['edges'][0]['variable'],'state':k[-1],
            'source_line':t['slice']['source_line'],'reasons':['no_unique_action_predecessor_or_action_role_unknown']})
    return {'schema':'plc.action_signal_merge.v1','source_sha256':graph.get('source_sha256'),
            'no_gt_access':True,'proposals':proposals,'reviews':reviews,
            'summary':{'positive_signal_tail_states':len(tail),'merged_intervals':len(proposals),
                       'review_records':len(reviews),'adapter':graph.get('adapter'),
                       'limits':'Native InlineST side-effect adapter; other formats/unknown coils are not auto-classified.'}}


def write_discovery(graph,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);result=discover(graph)
    (out/'signal_merge_lineage.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    write_csv(out/'signal_merge_candidates.csv',[p['row'] for p in result['proposals']],
              ['构成要素','动作','动作描述','开始标签','结束标签','状态','证据POU','证据节点','原始条件','控制器','分支数'])
    rr=[{**r,'reasons':';'.join(r['reasons'])} for r in result['reviews']]
    write_csv(out/'signal_merge_review.csv',rr,['scope','variable','state','network','source_line','comment','reasons'])
    return result


def export_collection(out,graph,prefix='prediction'):
    """Non-composite collection view, separate from the lossless atomic archive."""
    out=Path(out);p=out/f'{prefix}_atomic_pairs.csv'
    with p.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    model=discover(graph);by=defaultdict(list)
    for r in rows:by[key_pair(r)].append(r)
    suppress=set();new=[];audit=[]
    for prop in model['proposals']:
        # Never enlarge an output produced by a name/semantic filter with unrelated
        # stages. Both constituents must exist, or an actual merged pair be selected.
        mk=key_pair(prop['row']);ck=[key_pair(x) for x in prop['constituent_pairs']]
        semantic_view=prefix in ('process','sequence')
        available=mk in by or all(k in by for k in ck) or (semantic_view and ck[0] in by)
        if not available:continue
        matches=by.get(mk,[])
        if matches:merged=dict(matches[0])
        else:
            merged=dict(by[ck[0]][0]);merged.update(prop['row'])
            merged['原子对ID']=_id('A',mk)
        merged.update({'动作':'动作及后续信号传递','动作描述':prop['row']['动作描述'],
            '证据等级':'动作+信号收尾路径（待工艺复核）','全局退出等价':'不适用-计时区间合并',
            '合并ID':prop['merge_id'],'合并路径':' → '.join(map(str,[prop['action_start']]+prop['tail_states']+[prop['end_state']])),
            '计时使用方式':'从动作起点开始，到信号/数据收尾完成后结束；内部延时计入',
            '关联条件文件':'signal_merge_lineage.json','过程类型':'机械动作','统计对象':prop['row']['构成要素'],
            '起点含义':'前置实际动作阶段进入','终点含义':'后续信号/数据处理完成、进入下一状态',
            '说明':'应用用户采集粒度规则；不是把两个结束标签判为等价；原始分段保留在atomic/full文件。'})
        suppress.update(ck);suppress.add(mk)
        if semantic_view:
            semantic_roots=matches or by[ck[0]]
            for root in semantic_roots:
                copy=dict(merged)
                for field in ('构成要素','动作','过程类型','统计对象','起点含义','工艺依据','边界依据'):
                    if root.get(field):copy[field]=root[field]
                copy['动作描述']=root.get('动作描述','')+'；含后续信号/数据收尾'
                copy['原子对ID']=_id('A',[mk,root.get('原子对ID',''),copy.get('动作',''),copy.get('统计对象','')])
                new.append(copy)
        else:new.append(merged)
        audit.append({'合并ID':prop['merge_id'],'开始标签':merged['开始标签'],'结束标签':merged['结束标签'],
            '内部状态':';'.join(map(str,prop['tail_states'])),'路径':merged['合并路径'],
            '处理':'合并显示；原始分段保留','证据':'signal_merge_lineage.json'})
    kept=[dict(r,合并ID='',合并路径='') for r in rows if key_pair(r) not in suppress]
    result=kept+new
    result.sort(key=lambda r:key_pair(r))
    write_csv(out/f'{prefix}_collection_pairs.csv',result,list(rows[0])+['合并ID','合并路径'] if rows else ['开始标签','结束标签','合并ID','合并路径'])
    write_csv(out/f'{prefix}_signal_merge_audit.csv',audit,['合并ID','开始标签','结束标签','内部状态','路径','处理','证据'])
    stats={'input_atomic_pairs':len(rows),'collection_pairs':len(result),'merged_intervals_shown':len(new),
        'constituent_rows_hidden_in_collection_only':sum(key_pair(r) in suppress for r in rows),
        'raw_atomic_not_mutated':True,'no_gt_access':True,'main_output':f'{prefix}_collection_pairs.csv'}
    (out/f'{prefix}_collection_summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
    return stats

if __name__=='__main__':
    ap=argparse.ArgumentParser(description='source-only signal tail merge; no GT parameter')
    ap.add_argument('--graph',required=True);ap.add_argument('--out',required=True);ap.add_argument('--prefix',default='prediction')
    args=ap.parse_args();g=json.loads(Path(args.graph).read_text(encoding='utf-8'))
    d=write_discovery(g,args.out)
    print(json.dumps(d['summary'],ensure_ascii=False,indent=2))
