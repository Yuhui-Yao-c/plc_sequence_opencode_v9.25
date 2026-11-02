"""Prediction/GT isolation; one existing start/end PAIR per GT, never cross-row splicing.
Source graph normalization is symmetric, read-only and row-count preserving.
Normalized model-boundary matches are separate from original label matches and
NEVER imply identical PLC acquisition timestamps or physical completion.
"""
from pathlib import Path
from collections import Counter,defaultdict
import json,re
from boundary_logic import canonical,parse,comparisons,BoundarySyntaxError
from boundary_normalizer import normalize_rows,empty_graph,write_csv,RESOLVED
from table_io import prediction_records,truth_records
from feedback_successor import FeedbackResolver

def textkey(s):return re.sub(r'\s+','',str(s)).casefold()
def pairkey(r):return canonical(r.get('开始标签','')),canonical(r.get('结束标签',''))
def cat_for(g,p):
    if textkey(g['开始标签'])==textkey(p['开始标签']) and textkey(g['结束标签'])==textkey(p['结束标签']):return '严格起止命中',100
    if pairkey(g)==pairkey(p):return '布尔结构命中',96
    return '状态模型边界命中',90

def evaluate(pred,truth,graph=None,top_k=5):
    graph=graph or empty_graph();np,ps=normalize_rows(pred,graph);ng,gs=normalize_rows(truth,graph)
    bypair=defaultdict(list);bystart=defaultdict(list);byend=defaultdict(list)
    for j,r in enumerate(np):
        if r.get('开始标签') and r.get('结束标签'):bypair[pairkey(r)].append(j)
        bystart[canonical(r.get('开始标签',''))].append(j);byend[canonical(r.get('结束标签',''))].append(j)
    # Equivalent prediction duplicates form one capacity-one class. Equal keys create
    # disconnected complete bipartite classes; exact-first pairing is cardinality-optimal here.
    proposals=[];options=[]
    for i,g in enumerate(ng):
        opts=[]
        if g.get('开始标签') and g.get('结束标签') and g['边界规范化状态']!='待核验-表达式不支持':
            for j in bypair.get(pairkey(g),[]):
                category,score=cat_for(truth[i],pred[j]);opts.append((score,j,category))
        opts.sort(key=lambda x:(-x[0],x[1]));options.append(opts)
        if opts:proposals.append((-opts[0][0],i,opts[0][1],opts[0][2]))
    proposals.sort();used=set();assigned={}
    for neg,i,j,category in proposals:
        k=pairkey(np[j])
        if k in used:continue
        used.add(k);assigned[i]=(j,category,-neg)
    feedback_resolver=FeedbackResolver(graph)
    records=[];hits=[]
    for i,(g,graw) in enumerate(zip(ng,truth)):
        winner=assigned.get(i);possible=options[i]
        related=[]
        if possible:related=[(j,c,sc) for sc,j,c in possible]
        else:
            ids=bystart.get(canonical(g.get('开始标签','')),[])
            related=[(j,'同起点/终点不同',60) for j in ids]
            if not related:related=[(j,'同终点/起点不同',55) for j in byend.get(canonical(g.get('结束标签','')),[])]
        feedback=feedback_resolver.resolve(graw) if not winner and not possible else None
        feedback_match=None
        if feedback and len(feedback.rows)==1:
            js=bypair.get(pairkey(feedback.rows[0]),[])
            if js:
                feedback_match=js[0]
                related=[(j,'有条件的迁移替代',85) for j in js]+[x for x in related if x[0] not in js]
        if winner:j,category,score=winner;basis='原始完整标签对匹配' if category=='严格起止命中' else '布尔规范化后的完整标签对匹配' if category=='布尔结构命中' else '两侧独立按同一源码状态图规范化后，完整标签对一致；非采样时间戳证明'
        elif possible:category='一对一匹配冲突';score=0;basis='等价候选标签对已分配给其他GT，不能重复计分'
        elif feedback_match is not None:category='有条件的迁移替代';score=0;basis=feedback.reason
        elif g['边界规范化状态']=='待核验-表达式不支持':category='GT写法待核验';score=0;basis=g['边界转换说明']
        else:category=related[0][1] if related else '没有检索到相关候选';score=0;basis='仅找到单端相关项，不能拼接其他候选作为完整匹配' if related else '缺少匹配证据，不据此判定GT错误'
        candidate=pred[winner[0]] if winner else None
        rec={'人工编号':i+1,'动作描述':graw.get('动作描述',''),'GT开始':graw['开始标签'],'GT结束':graw['结束标签'],
            'GT规范化开始':g['开始标签'],'GT规范化结束':g['结束标签'],'最佳匹配':category,
            '分配状态':'已唯一分配' if winner else '冲突未计分' if possible else '未计分',
            '计入覆盖':'是' if winner else '否','覆盖层级':'状态模型' if winner and category=='状态模型边界命中' else '标签形式' if winner else '',
            '唯一匹配候选行号':candidate.get('预测源文件行号',winner[0]+2) if winner else '',
            '唯一匹配预测开始':candidate['开始标签'] if candidate else '',
            '唯一匹配预测结束':candidate['结束标签'] if candidate else '',
            '预测规范化开始':np[winner[0]]['开始标签'] if winner else '',
            '预测规范化结束':np[winner[0]]['结束标签'] if winner else '',
            'GT边界规范化状态':g['边界规范化状态'],'GT迁移证据ID':g['迁移证据ID'],
            '预测边界规范化状态':np[winner[0]]['边界规范化状态'] if winner else '',
            '人工源文件行号':graw.get('人工源文件行号',i+1),'证据解释':basis,'最高得分':score}
        rec.update({'反馈后继判定':feedback.status if feedback and feedback.applicable else '',
            '附加门控':'; '.join(b.get('附加门控','') for b in feedback.rows) if feedback else '',
            '参考候选行号':pred[feedback_match].get('预测源文件行号',feedback_match+2) if feedback_match is not None else '',
            '参考开始标签':pred[feedback_match]['开始标签'] if feedback_match is not None else '',
            '参考结束标签':pred[feedback_match]['结束标签'] if feedback_match is not None else ''})
        if feedback and feedback.applicable:
            rec['反馈后继证据ID']=';'.join(b.get('迁移证据ID','') for b in feedback.rows)
            rec['反馈后继阻断项']='；'.join(feedback.issues)
        records.append(rec)
        for rank,(j,c,sc) in enumerate(related[:top_k],1):
            hits.append({'人工编号':i+1,'排序':rank,'类别':c,'预测行号':pred[j].get('预测源文件行号',j+2),
              '预测开始':pred[j]['开始标签'],'预测结束':pred[j]['结束标签'],
              '预测规范化开始':np[j]['开始标签'],'预测规范化结束':np[j]['结束标签'],
              '是否唯一分配':'是' if winner and winner[0]==j else '否','依据':'同一行完整标签对；标准化细节见逐条表' if possible else '仅相关，不计分'})
    counts=Counter(r['最佳匹配'] for r in records)
    metrics={'version':'9.13','gt_rows':len(truth),'candidates':len(pred),'categories':dict(counts),
       'label_pair_matches':counts['严格起止命中']+counts['布尔结构命中'],
       'additional_source_model_matches':counts['状态模型边界命中'],'unique_scored_gt':len(assigned),
       'prediction_normalization':ps,'gt_normalization':gs,'source_sha256':graph.get('source_sha256'),
       'feedback_successor_matches_not_scored':counts['有条件的迁移替代'],
       'scope':'Each GT matches ONE existing pair; no cross-row path/splicing; duplicate equivalent pairs have capacity one.',
       'important':'Source-model match is not proof of physical action completeness or acquisition time equality.'}
    return records,hits,metrics,np,ng

def run(pred,gt,output,relations=None,top_k=5,graph=None):
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    records,hits,metrics,np,ng=evaluate(prediction_records(pred),truth_records(gt),graph,top_k)
    if relations:metrics['legacy_relations_note']='Legacy co-location/partial relation CSV not used as proof; supply --xml or strict --transition-graph.'
    write_csv(out/'gt_match_summary.csv',records);write_csv(out/'gt_match_hits.csv',hits)
    write_csv(out/'prediction_normalized_audit.csv',np);write_csv(out/'gt_normalized_audit.csv',ng)
    (out/'gt_match_metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
    return metrics
