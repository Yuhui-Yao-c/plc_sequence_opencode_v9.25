"""Read-only evaluation of an atomic prediction view.

One original GT is scored against ONE existing atomic pair only. Split OR sets
have branch diagnostics, not a relaxed numerator; no prefix/path stitching and
no generated prediction rows. Conditional local successors are not strict hits.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from pathlib import Path
import json
from atomic_boundaries import AtomicProjector, atomic, pair_key
from boundary_logic import canonical,parse,positive_states,BoundarySyntaxError
from boundary_normalizer import write_csv,empty_graph
from table_io import prediction_records,truth_records
from pair_evaluation import textkey

def evaluate(pred: list[dict], truth: list[dict],graph: dict | None=None,top_k: int=5):
    graph=graph or empty_graph();projector=AtomicProjector(graph)
    index=defaultdict(list);starts=defaultdict(list);invalid=[]
    for j,r in enumerate(pred):
        if atomic(r.get('开始标签','')) is None or atomic(r.get('结束标签','')) is None:
            invalid.append(j+1);continue
        index[pair_key(r['开始标签'],r['结束标签'])].append(j)
        starts[canonical(r['开始标签'])].append(j)
    if invalid:raise ValueError(f'原子评价输入包含复合/不支持标签，第{invalid[:10]}条；请使用完整候选评价或先导出原子视图')
    projected=[projector.project(g) for g in truth]
    proposals=[];eligible={};diagnostics=[]
    for i,(g,pr) in enumerate(zip(truth,projected)):
        if len(pr.rows)==1 and pr.strict_equivalence:
            b=pr.rows[0];key=pair_key(b['开始标签'],b['结束标签']);matches=index.get(key,[])
            if matches:
                j=matches[0];p=pred[j]
                if pair_key(g['开始标签'],g['结束标签'])==key:
                    exact=textkey(g['开始标签'])==textkey(p['开始标签']) and textkey(g['结束标签'])==textkey(p['结束标签'])
                    cat,score=('严格起止命中',100) if exact else ('布尔结构命中',96)
                else:cat,score='状态模型边界命中',90
                proposals.append((-score,i,j,key,cat));eligible[i]=(j,key,cat)
    assigned={};used={}
    for neg,i,j,key,cat in sorted(proposals):
        if key in used:continue
        assigned[i]=(j,cat,-neg);used[key]=i
    results=[];hits=[]
    for i,(g,pr) in enumerate(zip(truth,projected)):
        branch_hits=[]
        generated_entries={e.args[1] for b in pr.rows if (e:=atomic(b['开始标签'])) is not None and e.kind=='eq'}
        entries_complete=not pr.entries or set(pr.entries).issubset(generated_entries)
        all_found=bool(pr.rows) and entries_complete
        for k,b in enumerate(pr.rows,1):
            key=pair_key(b['开始标签'],b['结束标签']);matches=index.get(key,[])
            if not matches:all_found=False
            j=matches[0] if matches else None
            if j is not None:branch_hits.append(j)
            diagnostics.append({'人工编号':i+1,'分支编号':k,'GT原始开始':g['开始标签'],'GT原始结束':g['结束标签'],
                '分支开始':b['开始标签'],'分支结束':b['结束标签'],
                '类别':'分支已有预测-仅诊断' if matches else '分支缺失',
                '预测原子对ID':pred[j].get('原子对ID','') if j is not None else '',
                '预测行号':pred[j].get('预测源文件行号',j+2) if j is not None else '',
                '迁移证据ID':b.get('迁移证据ID',''),'全局退出等价':'是' if pr.strict_equivalence else '否/未证明',
                '说明':'每分支查找完整单行；不据此给原始复合GT计严格单对分数'})
        winner=assigned.get(i);selected=winner[0] if winner else (branch_hits[0] if len(pr.rows)==1 and branch_hits else None)
        if winner:cat=winner[1];why='同一行完整单对匹配；状态模型类仍不证明在线CT时间戳'
        elif i in eligible:cat='一对一匹配冲突';why='该完整候选对已给其他GT计分；不允许重复占用'
        elif len(pr.rows)>1 and all_found:
            cat='复合分支齐全-仅诊断';why='所有已推导分支均有单行预测，但原GT不能由一对标签表达；不计原GT单对覆盖'
        elif (len(pr.rows)>1 or not entries_complete) and branch_hits:
            cat='复合分支部分命中';why='仅部分分支找到完整预测；不能将任一分支算成整条OR正确'
        elif len(pr.rows)==1 and all_found:
            cat='有条件的迁移替代';why=(pr.reason if pr.status.startswith('反馈') else '局部源码后继存在，但复位/未知写入等使全局退出等价未证明；不计严格覆盖')
        elif pr.status=='写法待核验':cat='GT写法待核验';why=pr.reason
        elif pr.status in ('复合起点待核验','控制器或集合待核验','OR集合入口待核验','周期待核验','作用域待核验'):
            cat='复合语义待核验';why=pr.reason
        else:cat='没有检索到相关候选';why=pr.reason or '无完整单对匹配，不据此判GT错误'
        related=list(dict.fromkeys(branch_hits))
        if not related:
            related.extend(starts.get(canonical(g['开始标签']),[]))
        if not related:
            try:
                entry=positive_states(parse(g['开始标签']))
                if entry:
                    for n in sorted(entry[1]):related.extend(starts.get(canonical(f'{entry[0]}={n}'),[]))
            except BoundarySyntaxError:pass
        if cat=='没有检索到相关候选' and related:
            cat='同起点/终点不同';why+='；找到同起点记录，但不认为等价'
        p=pred[selected] if selected is not None else {}
        results.append({'人工编号':i+1,'GT开始':g['开始标签'],'GT结束':g['结束标签'],
            '最佳匹配':cat,'计入覆盖':'是' if winner else '否',
            '分配状态':'已唯一分配' if winner else '冲突未计分' if i in eligible else '未计分',
            '覆盖层级':'状态模型' if winner and cat=='状态模型边界命中' else '标签形式' if winner else '',
            '唯一匹配候选行号':p.get('预测源文件行号','') if winner else '',
            '唯一匹配预测开始':p.get('开始标签','') if winner else '',
            '唯一匹配预测结束':p.get('结束标签','') if winner else '',
            '参考候选行号':p.get('预测源文件行号',''),
            '参考开始标签':p.get('开始标签',''),'参考结束标签':p.get('结束标签',''),
            '需比较分支数':len(pr.rows),'已有预测分支数':len(branch_hits),
            '全部已知分支存在':'是' if all_found else '否','所有OR入口有后继':'是' if entries_complete else '否',
            '原子化判定':pr.status,'全局等价检查':'通过' if pr.strict_equivalence else '未证明',
            '反馈后继判定':pr.status if pr.status.startswith('反馈') else '',
            '反馈原终点':g['结束标签'] if pr.status.startswith('反馈') else '',
            '附加门控':'; '.join(b.get('附加门控','') for b in pr.rows if b.get('附加门控')),
            'GT迁移证据ID':';'.join(sorted({eid for b in pr.rows for eid in b.get('迁移证据ID','').split(';') if eid})),
            '人工源文件行号':g.get('人工源文件行号',i+1),'证据解释':why,
            '阻断项':'；'.join(pr.issues)[:6000]})
        for rank,j in enumerate(related[:top_k],1):
            hits.append({'人工编号':i+1,'排序':rank,'类别':cat,'预测行号':pred[j].get('预测源文件行号',j+2),
                '预测开始':pred[j]['开始标签'],'预测结束':pred[j]['结束标签'],
                '是否唯一分配':'是' if winner and winner[0]==j else '否','依据':why})
    counts=Counter(r['最佳匹配'] for r in results)
    metrics={'version':'9.13','evaluation_mode':'atomic_single_pair_with_branch_diagnostics',
        'gt_rows':len(truth),'candidates':len(pred),
        'unique_candidate_pairs':len(index),'categories':dict(counts),
        'label_pair_matches':counts['严格起止命中']+counts['布尔结构命中'],
        'additional_source_model_matches':counts['状态模型边界命中'],
        'unique_scored_gt':len(assigned),
        'gt_rows_with_all_known_atomic_branches_present':sum(r['全部已知分支存在']=='是' for r in results),
        'branch_diagnostic_rows':len(diagnostics),
        'branch_diagnostic_hits':sum(r['类别']=='分支已有预测-仅诊断' for r in diagnostics),
        'conditional_matches_not_scored':counts['有条件的迁移替代'],
        'feedback_successor_matches_not_scored':sum(r['最佳匹配']=='有条件的迁移替代' and bool(r['反馈后继判定']) for r in results),
        'compound_groups_not_scored':counts['复合分支齐全-仅诊断'],
        'source_sha256':graph.get('source_sha256'),
        'scope':'原始GT严格分数仅允许一条已有完整候选；OR分支表只作诊断，不放宽为跨行拼接',
        'important':'全部已知分支存在≠所有真实分支已发现≠物理动作覆盖或在线计时等价；分支分母不同，不与旧411/523混算'}
    return results,hits,metrics,diagnostics

def run(pred,gt,output,graph=None,top_k=5):
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    rr,hh,mm,dd=evaluate(prediction_records(pred),truth_records(gt),graph,top_k)
    write_csv(out/'gt_match_summary.csv',rr);write_csv(out/'gt_match_hits.csv',hh)
    write_csv(out/'gt_atomic_branch_diagnostics.csv',dd,
              list(dd[0]) if dd else ['人工编号','分支编号','类别'])
    (out/'gt_match_metrics.json').write_text(json.dumps(mm,ensure_ascii=False,indent=2),encoding='utf-8')
    return mm
