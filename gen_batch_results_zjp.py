# -*- coding: utf-8 -*-
"""Batch result generator for 转接片 run.
Reads each batches/B*.input.json, produces B*.result.json following RESULT_SCHEMA.md.

Classification is conservative and source-grounded:
- source_supported_transition candidates: classified by 构成要素/原始条件 keywords
  into real process types (机械动作/加工保持/检测测量/工艺等待/交接数据).
- weak hypotheses (adjacency_hypothesis/reset_hypothesis/state_exit): 未分类, marked candidate.
- events (same_body_observation with empty guard/next_state): unresolved_events
  (co-occurrence is not causation per AGENT_PROMPT).

Never reads GT/manual. All labels/IDs come verbatim from the batch input candidates.
"""
import json, os, glob, re, sys

BDIR = r'outputs\转接片\batches'

# keyword -> process type. Order matters: more specific first.
# Based on 构成要素 (Chinese mechanism names) and 原始条件 (guard signals).
CONSTITUENT_RULES = [
    # 加工保持 (processing hold) — specific processing actions first
    (r'焊接|点焊|缝焊|激光焊|超声波焊|焊印', '加工保持'),
    (r'激光器|激光', '加工保持'),
    (r'点胶|备胶|贴胶|喷胶|蓝胶', '加工保持'),
    (r'刻码|打码', '加工保持'),
    (r'对焦', '加工保持'),
    # 检测测量
    (r'检测|检查|视觉|CCD|测厚|测量|探伤|品质判|判别|焊印CCD|极耳CCD', '检测测量'),
    (r'除尘', '检测测量'),
    # 工艺等待
    (r'等待|Wait|延时|稳定|冷却', '工艺等待'),
    # 交接数据
    (r'交接|握手|Handshake|同步|中转', '交接数据'),
    # 周期汇总
    (r'周期|节拍|Cycle|循环线', '周期汇总'),
    # 机械动作 — material handling & mechanism movements
    (r'开卷|放卷|卷料|送料|送线|供料|供线', '机械动作'),
    (r'上料|下料|取料|放料|拾取|放置|进料|出料', '机械动作'),
    (r'搬运|输送|传送|流水线|输送带|移载|移栽', '机械动作'),
    (r'收料|收卷|堆叠|码垛|叠片|叠料|堆栈', '机械动作'),
    (r'夹具|夹紧|松开|夹持|Clamp|Unclamp', '机械动作'),
    (r'气缸|Cyl|Cylinder', '机械动作'),
    (r'伺服|轴|Axis|Location|Move|定位', '机械动作'),
    (r'机器人|升降机|中转|平台|翻转|整形|拉带|板链|链线|支架|吸塑盘|保护盖|更换|电芯|顶盖|机构', '机械动作'),
    (r'NG拉带|NG', '机械动作'),
    (r'进料平台|出料平台|上料位', '机械动作'),
]
COND_RULES = [
    (r'WeldStart|WeldSeal|Focusing_Weld', '加工保持'),
    (r'SprayGlue|Blow_Glue|Blow_SprayGlue|Blow_Nitrogen', '加工保持'),
    (r'PosDone|Homed|AxisX|Axis_X|AxisY|Axis_Y|Axis_Z|Axis_R|Move|Location_Auto|MC_Move|ToPos|Home_Auto|AbsModel', '机械动作'),
    (r'Clamp|Unclamp|Cyl|气缸|Cy_', '机械动作'),
    (r'Va_Plate|Va_Cover|Va_Glue', '机械动作'),
    (r'Mo_Load|Mo_UnLoad|Mo_Translation|StartRight', '机械动作'),
    (r'Robot.*ToPos|Robot\dTo', '机械动作'),
    (r'焊|Weld|超声', '加工保持'),
    (r'检测|检查|Inspect|Measure|Vision|CCD', '检测测量'),
    (r'Wait|延时|Timer|TON|稳定|冷却', '工艺等待'),
    (r'LinkOut|Module_RunEndStop', '交接数据'),
    (r'Golytec|StationMotion', '机械动作'),
]

WEAK_STATES = {'adjacency_hypothesis', 'reset_hypothesis', 'state_exit',
               'feedback_hypothesis', 'feedback_same_body_hypothesis',
               'action_event_unverified', 'observed_same_network_transition'}


def classify(c):
    """Return (过程类型, 说明) based on source evidence fields."""
    status = c.get('状态', '')
    desc = c.get('动作描述', '')
    const = c.get('构成要素', '')
    cond = c.get('原始条件', '')
    pou = c.get('证据POU', '')
    boundary_note = c.get('边界转换说明', '')

    # Weak hypotheses: honest 未分类
    if status in WEAK_STATES:
        return ('未分类', '假设性候选(%s)；%s' % (status, desc))

    # source_supported_transition: try keyword classification from 构成要素 + 证据POU
    text = const + ';' + pou
    for pat, ptype in CONSTITUENT_RULES:
        if re.search(pat, text):
            return (ptype, '源码局部状态迁移(%s)；按构成要素/POU归类为%s' % (status, ptype))

    # fall back to 原始条件 guard signals
    for pat, ptype in COND_RULES:
        if re.search(pat, cond):
            return (ptype, '源码局部状态迁移(%s)；按门控信号归类为%s' % (status, ptype))

    # reset to 0 from a non-zero state without strong action evidence
    end_label = c.get('结束标签', '')
    if end_label.endswith('=0'):
        return ('未分类', '源码局部状态迁移(%s)回到0；机构动作待核验' % status)

    return ('未分类', '源码局部状态迁移(%s)；机构类型未识别，待核验' % status)


def make_step(c):
    cid = c.get('候选ID', '')
    ptype, note = classify(c)
    start = c.get('开始标签', '')
    end = c.get('结束标签', '')
    const = c.get('构成要素', '')
    cond = c.get('原始条件', '')
    pou = c.get('证据POU', '')
    enode = c.get('证据节点', '')
    boundary_status = c.get('边界规范化状态', '')
    boundary_note = c.get('边界转换说明', '')
    status = c.get('状态', '')

    # 统计对象: prefer 构成要素 Chinese name; fallback to 控制器
    obj = const if const.strip() else c.get('控制器', '')
    # 起点含义 / 终点含义: describe state entry/exit
    start_meaning = '进入状态 %s' % start
    if end.endswith('=0'):
        end_meaning = '状态回到0(本次阶段结束/复位)'
    else:
        end_meaning = '进入状态 %s' % end
    # 工艺依据: source POU + node + guard condition
    evidence_parts = []
    if pou:
        evidence_parts.append('证据POU:%s' % pou)
    if enode:
        evidence_parts.append('证据节点:%s' % enode)
    if cond:
        evidence_parts.append('门控:%s' % cond)
    proc_basis = '；'.join(evidence_parts) if evidence_parts else ('状态:%s' % status)
    # 边界依据: boundary normalization status + note
    if boundary_status:
        boundary_basis = '%s；%s' % (boundary_status, boundary_note)
    else:
        boundary_basis = '未提供边界规范化信息'

    return {
        '构成要素': const,
        '动作': c.get('动作', ''),
        '动作描述': c.get('动作描述', ''),
        '开始标签': start,
        '结束标签': end,
        '核验状态': 'candidate',
        '证据候选ID': [cid] if cid else [],
        '证据事件ID': [],
        '说明': note,
        '过程类型': ptype,
        '统计对象': obj,
        '起点含义': start_meaning,
        '终点含义': end_meaning,
        '工艺依据': proc_basis,
        '边界依据': boundary_basis,
    }


def process_batch(fp):
    with open(fp, encoding='utf-8') as f:
        d = json.load(f)
    bid = d['batch_id']
    steps = []
    unresolved = []
    for c in d.get('candidates', []):
        steps.append(make_step(c))
    for e in d.get('events', []):
        eid = e.get('事件ID', '')
        cmd = e.get('command', '')
        rel = e.get('relation', '')
        guard = e.get('guard', '')
        nxt = e.get('next_state', '')
        reason = '同本体/同网络共现观察(command=%s, relation=%s)；guard=%r next_state=%r 为空，无法据共现证明因果，需源码控制流核验' % (cmd, rel, guard, nxt)
        unresolved.append({'event_id': eid, 'reason': reason})
    result = {
        'batch_id': bid,
        'steps': steps,
        'unresolved_events': unresolved,
        'notes': '候选%d条全部入steps(核验状态=candidate)；事件%d条入unresolved(共现非因果)。过程类型按构成要素/POU/门控关键字保守归类，弱假设标未分类。' % (len(steps), len(unresolved)),
    }
    out_path = fp.replace('.input.json', '.result.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return bid, len(steps), len(unresolved)


def main():
    files = sorted(glob.glob(os.path.join(BDIR, '*.input.json')))
    print('processing %d batches...' % len(files))
    total_s = total_u = 0
    ptype_count = {}
    for fp in files:
        bid, ns, nu = process_batch(fp)
        total_s += ns
        total_u += nu
        # count process types
        with open(fp.replace('.input.json', '.result.json'), encoding='utf-8') as f:
            r = json.load(f)
        for s in r['steps']:
            pt = s.get('过程类型', '')
            ptype_count[pt] = ptype_count.get(pt, 0) + 1
    print('done. total steps=%d, unresolved=%d' % (total_s, total_u))
    print('process type distribution:')
    for k, v in sorted(ptype_count.items(), key=lambda x: -x[1]):
        print('  %4d  %s' % (v, k))


if __name__ == '__main__':
    main()
