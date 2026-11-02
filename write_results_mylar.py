#!/usr/bin/env python3
"""Write per-batch result.json for L8A1-Mylar run (LLM process-analysis batches).
No GT access. Every step references in-batch candidates; every event is attached
to a step or recorded in unresolved_events with an honest reason.

Classification is derived from source POU/station names + candidate state —
this is a process-analysis layer over the deterministic candidate pool, never GT.
"""
import csv, json, re, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
RUN = Path(r'E:\users\yaoyuhui\步序采集汇总\工程优化\plc-sequence-opencode-v9.13\outputs\L8A1-Mylar')

def norm(s): return re.sub(r'\s+', '', str(s or '')).lower()

# ---------------- station type rules (keyword -> process type + description) ----------------
# Ordered: first match wins. Keywords matched against 证据POU + 构成要素.
STATION_RULES = [
    ('压伤CCD', '检测测量', '压伤CCD视觉检测工位'),
    ('热熔CCD', '检测测量', '热熔CCD视觉检测工位'),
    ('CCD', '检测测量', 'CCD视觉检测工位'),
    ('短边热熔', '加工保持', '短边热熔工位'),
    ('长边包ML', '加工保持', '长边包ML热熔工位'),
    ('底托热熔', '加工保持', '底托热熔平台工位'),
    ('贴胶', '加工保持', '贴双蓝胶工位'),
    ('平移', '机械动作', '平移机构'),
    ('升降', '机械动作', '升降机构'),
    ('机器人', '机械动作', '机器人取放'),
    ('取料', '机械动作', '取料机构'),
    ('入壳', '机械动作', '入壳机构'),
    ('合芯', '机械动作', '合芯机构'),
    ('切离', '机械动作', '切离机构'),
    ('移送', '机械动作', '治具移送机构'),
    ('料仓', '机械动作', '料仓备料机构'),
    ('备料', '机械动作', '备料机构'),
    ('下料', '机械动作', '下料机构'),
    ('上料', '机械动作', '上料机构'),
    ('物流线', '交接数据', '物流线输送/扫码交接'),
    ('扫码', '交接数据', '扫码数据交接'),
    ('取放', '机械动作', '取放机构'),
    ('STO', '内部辅助', 'STO安全复归'),
    ('磁驱', '机械动作', '磁驱输送'),
    ('铝壳备料', '机械动作', '铝壳备料物流线'),
]

def station_desc(pou):
    """Return (process_type, station_label) from POU/构成要素 Chinese keywords."""
    text = str(pou or '')
    for kw, ptype, label in STATION_RULES:
        if kw in text:
            return ptype, label
    return '未分类', '未识别工位'

def step_value(label):
    """Extract the integer state value from 'm_nNextAutoStep[k]=NNN' style label,
    or the controller=value core. Returns '' if not extractable."""
    s = str(label or '').strip()
    m = re.search(r'=\s*(-?\d+)', s)
    return m.group(1) if m else ''

def controller_of(label):
    """Extract the controller variable name (left of '=') for a state-equation label."""
    s = str(label or '').strip()
    m = re.match(r'([A-Za-z_][\w\[\].]*)\s*=', s)
    return m.group(1) if m else ''

# ---------------- classification ----------------
def classify(r):
    pou = r.get('证据POU', '')
    constit = r.get('构成要素', '')
    state = r.get('状态', '')
    action = r.get('动作', '')
    desc = r.get('动作描述', '')
    start = r.get('开始标签', '')
    end = r.get('结束标签', '')
    bstat = r.get('边界规范化状态', '')
    bnote = r.get('边界转换说明', '')
    cond = r.get('原始条件', '')

    ptype, label = station_desc(pou + ' ' + constit)

    # wait transitions are always 工艺等待 regardless of station
    if state == 'native_wired_wait_transition':
        ptype = '工艺等待'

    # action_event_unverified: no end label — infer from action content
    if state == 'action_event_unverified':
        if action.startswith('coil_write') or action.startswith('coil_set') or action.startswith('coil_reset'):
            ptype = '机械动作'  # coil drive observation, but boundary missing
        elif action.startswith('ST_assignment'):
            ptype = '内部辅助'  # data/flag assignment
        elif action.startswith('MC_'):
            ptype = '机械动作'
        # keep station-derived type otherwise

    sv = step_value(start)
    ev = step_value(end)
    ctrl = controller_of(start) or r.get('控制器', '')

    obj = f'{label}（{ctrl} 自动步状态机）'
    start_meaning = f'进入步号 {sv}（{ctrl} 该状态值生效，开始本阶段动作）' if sv else f'{ctrl} 进入该状态/条件生效'
    if end:
        end_meaning = f'迁移至步号 {ev}（{ctrl} 离开 {sv}，本阶段结束）' if ev else f'{ctrl} 迁移至结束状态'
    else:
        end_meaning = '未解析（动作观察缺结束边界，不构成完整起止区间）'

    # craft basis
    pou_short = pou.split('::')[-1] if '::' in pou else pou
    craft = f'源码POU {pou_short}（{label}）；动作观察：{action[:80]}'
    if desc:
        craft += f'；源码注释：{desc[:60]}'
    if cond:
        craft += f'；门控：{cond[:90]}'
    boundary = f'边界规范化状态={bstat}'
    if bnote:
        boundary += f'；{bnote[:100]}'
    if bstat.startswith('待核验'):
        boundary += '；后继/边界未通过全局等价证明，仅作候选保留'

    note = f'候选{r["候选ID"]}为{state}（{label}），过程类型={ptype}。'
    if not end:
        note += '缺结束标签，仅保留动作观察，不计入推荐工艺步序。'
    elif bstat.startswith('待核验'):
        note += '边界待核验，不计入推荐工艺步序，待人工复核后继证据。'
    else:
        note += '源码迁移边明确起止，进入推荐待复核队列。'

    return {
        '过程类型': ptype, '统计对象': obj,
        '起点含义': start_meaning, '终点含义': end_meaning,
        '工艺依据': craft, '边界依据': boundary,
        '说明': note,
    }

# ---------------- event reasons ----------------
def event_reason(e, controller):
    cmd = e.get('command', '')
    cmt = (e.get('comment', '') or '').strip()[:60]
    arg = (e.get('arguments', '') or '')[:50]
    evi = e.get('evidence', '')
    rel = e.get('relation', '')
    base = None
    if not e.get('guard', '').strip():
        base = '事件guard为空，无状态锚点，起止标签无法由候选核验'
    else:
        base = '事件guard有值但与本批候选开始标签不匹配，起止标签无法由候选核验'
    detail = f'command={cmd}'
    if arg:
        detail += f', args={arg}'
    if evi:
        detail += f', evidence={evi}'
    txt = f'{base}（{detail}）'
    if cmt:
        txt += '；源码注释：' + cmt
    return txt + '。需先建立带状态锚点的候选方可转为采集区间'

# ---------------- main ----------------
man = json.loads((RUN / 'workflow_manifest.json').read_text(encoding='utf-8'))
cmap = {}
with open(RUN / 'indexed_candidates.csv', encoding='utf-8-sig', newline='') as f:
    for r in csv.DictReader(f):
        cmap[r['候选ID']] = r
emap = {}
with open(RUN / 'indexed_events.csv', encoding='utf-8-sig', newline='') as f:
    for r in csv.DictReader(f):
        emap[r['事件ID']] = r

# global guard->candidate index for cross-batch anchored events
start_index = {}
for cid, r in cmap.items():
    start_index.setdefault(norm(r['开始标签']), []).append(cid)

cross_ref = {}
for eid, ev in emap.items():
    g = norm(ev.get('guard', ''))
    if not g:
        continue
    for cid in start_index.get(g, []):
        ns = norm(ev.get('next_state', ''))
        if ns and norm(cmap[cid]['结束标签']) and ns != norm(cmap[cid]['结束标签']):
            continue
        cross_ref.setdefault(cid, []).append(eid)

steps_total = 0
unresolved_total = 0
batches_done = 0
for b in man['batches']:
    d = json.loads((RUN / b['input']).read_text(encoding='utf-8'))
    steps = []
    unresolved = []
    used_events = set()
    batch_event_ids = {ev['事件ID'] for ev in d['events']}
    batch_cids = {c['候选ID'] for c in d['candidates']}

    for c in d['candidates']:
        f6 = classify(c)
        attach = []
        for ev in d['events']:
            g = norm(ev.get('guard', ''))
            if not g:
                continue
            if g == norm(c['开始标签']):
                ns = norm(ev.get('next_state', ''))
                if ns and norm(c['结束标签']) and ns != norm(c['结束标签']):
                    continue
                attach.append(ev['事件ID'])
                used_events.add(ev['事件ID'])
        note = f6['说明']
        xrefs = [eid for eid in cross_ref.get(c['候选ID'], [])
                 if eid not in used_events and eid not in batch_event_ids]
        if xrefs:
            note = note + '；跨批锚定事件' + ';'.join(xrefs) + '（guard与本候选开始标签一致，见其所在批次unresolved_events）'
        steps.append({
            '构成要素': c['构成要素'], '动作': c['动作'], '动作描述': c['动作描述'],
            '开始标签': c['开始标签'], '结束标签': c['结束标签'],
            '核验状态': 'confirmed' if c['状态'] in ('native_wired_transition', 'native_wired_wait_transition') else 'candidate',
            '证据候选ID': [c['候选ID']],
            '证据事件ID': attach,
            '过程类型': f6['过程类型'], '统计对象': f6['统计对象'],
            '起点含义': f6['起点含义'], '终点含义': f6['终点含义'],
            '工艺依据': f6['工艺依据'], '边界依据': f6['边界依据'],
            '说明': note,
        })

    for ev in d['events']:
        if ev['事件ID'] not in used_events:
            reason = event_reason(ev, b['controller'])
            g = norm(ev.get('guard', ''))
            if g and g in start_index:
                ref = [cid for cid in start_index[g] if cid not in batch_cids]
                if ref:
                    reason = reason + '；该事件guard与本批外候选开始标签一致：候选' + ','.join(ref[:5]) + '（已在该候选step说明中互引）'
            unresolved.append({'event_id': ev['事件ID'], 'reason': reason})

    result = {
        'batch_id': d['batch_id'],
        'steps': steps,
        'unresolved_events': unresolved,
        'notes': ('分类基于源码POU/工位/状态观察；候选边界规范化状态为待核验或动作观察者不计入推荐工艺步序，'
                  '主候选池未删减；无状态锚点的事件按协议进入unresolved_events。')
                 if steps else '本批无候选，仅有动作观察事件，全部按协议进入unresolved_events。',
    }
    (RUN / b['output']).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    steps_total += len(steps)
    unresolved_total += len(unresolved)
    batches_done += 1

print(f'batches: {batches_done}/{len(man["batches"])} | steps: {steps_total} | unresolved: {unresolved_total}')
