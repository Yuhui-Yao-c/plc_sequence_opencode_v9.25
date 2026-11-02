"""Formal review contract, not a keyword-based physical-action classifier.
Promoting a recommendation needs four explicit answers and non-adjacency evidence.
Raw and filtered candidate pools are NEVER deleted by this module.
"""
ALLOWED={'机械动作','加工保持','检测测量','工艺等待','交接数据','周期汇总'}
FIELDS=['过程类型','统计对象','起点含义','终点含义','工艺依据','边界依据']
WEAK={'adjacency_hypothesis','reset_hypothesis','state_exit','feedback_hypothesis',
      'feedback_same_body_hypothesis','action_event_unverified','observed_same_network_transition'}

def review(row,candidates,events):
    problems=[]
    if row.get('过程类型') not in ALLOWED:problems.append('未指定实际过程类型，或是内部辅助/未分类')
    for f in FIELDS[1:]:
        if not str(row.get(f,'')).strip():problems.append('缺少'+f)
    if not row.get('开始标签') or not row.get('结束标签'):problems.append('起止标签不完整')
    if row.get('边界规范化状态','').startswith('待核验'):problems.append('边界规范化尚未通过')
    cids=row.get('证据候选ID',[]);eids=row.get('证据事件ID',[])
    if isinstance(cids,str):cids=[x for x in cids.split(';') if x]
    if isinstance(eids,str):eids=[x for x in eids.split(';') if x]
    strong=any(c in candidates and candidates[c].get('状态') not in WEAK and
               ('wired' in candidates[c].get('状态','') or candidates[c].get('状态') in ('source_supported_transition','cross_fb_transport_candidate')) for c in cids)
    strong=strong or any(e in events and events[e].get('evidence') in ('plcopen_wired_guard','st_structured_guard','local_control_verified','smc2_grid_guard','smc2_embedded_st_guard','omron_native_wired_guard','omron_native_st_guard','source_cross_fb_binding_and_gate') for e in eids)
    if not strong:problems.append('缺少非邻接/非同域共现的控制来源')
    # A contract-complete row is still a recommendation, never runtime verification.
    return {'工艺契约结果':'推荐待复核' if not problems else '待补充证据',
            '筛选原因':'；'.join(problems) if problems else '四项说明齐全并有局部来源；实际采集和物理完成仍需复核'}
