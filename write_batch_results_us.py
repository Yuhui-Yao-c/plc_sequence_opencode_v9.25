#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write per-batch result.json for 超声_XM22PPM_L5B run (LLM process-analysis batches).
No GT access. Every step references in-batch candidates; every event is attached
to a step or recorded in unresolved_events with an honest reason.

Run: python write_batch_results_us.py
"""
import csv, json, re, sys, collections
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
RUN = Path(r'outputs\超声_XM22PPM_L5B')


def norm(s):
    return re.sub(r'\s+', '', str(s or '')).lower()


def main_ce(ce):
    """取构成要素分号前的主要工序段名。"""
    return (ce or '').split(';')[0].strip()


# ---------------- 门控关键变量摘要 ----------------
def guard_summary(cond):
    """从原始条件中提取工艺相关关键变量类型，作为工艺依据补充（不导出为标签）。"""
    if not cond:
        return ''
    c = str(cond)
    parts = []
    if re.search(r'SV\d+\.PosOK', c):
        parts.append('伺服轴到位(PosOK)')
    if re.search(r'RMOut\d+\.(Clamping_Signal|Open_Signal)', c):
        parts.append('夹具夹紧/开信号')
    if re.search(r'CY\d+\.LimitPos', c):
        parts.append('气缸限位')
    if re.search(r'EM\d+_[A-Za-z0-9_]+\.(Check|Flag)', c):
        parts.append('工站使能(Check/Flag)')
    if re.search(r'ST\d+\.AutoRun', c):
        parts.append('自动运行')
    if re.search(r'ST\d+\.Suspend', c):
        parts.append('暂停')
    if re.search(r'MES\.|Catl_MesData', c):
        parts.append('MES数据交互')
    if re.search(r'WLX\d+\.', c):
        parts.append('物流线信号')
    if re.search(r'Window\[\d+\]\.Enter', c):
        parts.append('工位进入许可')
    if re.search(r'ST\d+\.Ready|ST\d+\.Check|ST\d+\.Flag|ST\d+\.Done', c):
        parts.append('工位就绪/完成握手')
    if re.search(r'HJXHX_ST\d+\.', c):
        parts.append('焊接工位握手')
    if re.search(r'Barcode|BarCode|BCR', c, re.I):
        parts.append('条码')
    if re.search(r'TON|Timer', c):
        parts.append('定时等待')
    if re.search(r'System_Var\.Mode_', c):
        parts.append('模式标志')
    return '、'.join(parts)


# ---------------- 工序段分类 ----------------
def segment_info(r):
    """返回 (工序段名, 过程类型, 统计对象前缀, 工序类别说明)。"""
    ce = main_ce(r['构成要素'])
    ctrl = r['控制器']
    state = r['状态']
    # 跨FB输送优先
    if state == 'cross_fb_transport_candidate':
        return (ce, '工艺等待', '跨功能块输送/交接', 'cross_fb_transport')
    # 焊接
    if '铜焊' in ce or '铝焊' in ce:
        metal = '铜' if '铜焊' in ce else '铝'
        return (ce, '加工保持', metal + '焊接工序段', 'weld')
    # 贴胶
    if re.search(r'拉胶|备胶|贴胶|胶循环|焊印检测|蓝胶检测', ce):
        return (ce, '机械动作', '贴胶工序段', 'glue')
    # 检测/扫码
    if re.search(r'扫码', ce):
        return (ce, '交接数据', '条码扫码工序段', 'scan')
    if re.search(r'检测|抛NG|焊印|蓝胶', ce):
        return (ce, '检测测量', '检测工序段', 'inspect')
    if re.search(r'接驳', ce):
        return (ce, '交接数据', '接驳交接工序段', 'docking')
    # 物料搬运
    if re.search(r'上盘|下盘|移栽|横移|滚筒|夹爪|蜘蛛手|转运|上片|下料|中转|机台|Run', ce):
        return (ce, '机械动作', '物料搬运工序段', 'transport')
    return (ce, '机械动作', '工序段', 'default')


def F(ptype, obj, start, end, craft, boundary, note=''):
    return {'过程类型': ptype, '统计对象': obj, '起点含义': start, '终点含义': end,
            '工艺依据': craft, '边界依据': boundary, '说明': note}


def classify(r):
    ce = main_ce(r['构成要素'])
    seg, ptype, obj_prefix, cat = segment_info(r)
    state = r['状态']
    bstat = r['边界规范化状态']
    cond = r['原始条件'] or ''
    pou = r['证据POU'] or ''
    tid = r['迁移证据ID'] or ''
    bnote = r['边界转换说明'] or ''
    gsum = guard_summary(cond)
    ctrl = r['控制器']

    # ---- 跨FB输送 ----
    if cat == 'cross_fb_transport':
        start_lbl = r['开始标签']
        end_lbl = r['结束标签']
        ev_node = r['证据节点']
        craft = ('跨功能块输送协议：发车标志%s置位/复位生命周期与接收门控见 '
                 'cross_fb_transport_evidence.json#%s；POU=%s；发车侧→接收焊接工位') % (
                    start_lbl, ev_node, pou)
        boundary = ('单BOOL发车标志→单状态等式，非复合标签；边界规范化=%s。'
                    '发车标志复位后计时仍需锁存；触发到接收状态入口含实际输送、稳定、互锁与数据等待，'
                    '非纯运动时长；异常取消与本次循环配对另行核验') % bstat
        return F('工艺等待',
                 '跨功能块输送/交接——发车标志%s至接收工位状态入口%s（含输送、稳定、互锁与等待焊接准备）' % (start_lbl, end_lbl),
                 '发车标志%s触发（本次请求开始事件，锁存起点）' % start_lbl,
                 '接收工位进入状态%s（本次进入目标阶段事件，可能晚于到达反馈，含接收许可等待）' % end_lbl,
                 craft, boundary,
                 '跨FB触发—到达—接收区间；不读GT；与接收控制器同批；不并入后续焊接动作')

    # ---- 边界依据按状态 ----
    if state == 'source_supported_transition':
        gpart = ('；门控含%s' % gsum) if gsum else ''
        boundary = ('源码局部迁移%s（POU=%s）%s；边界规范化=%s。'
                    '终点已是明确状态；局部迁移不证明全局退出等价或采样时间戳相等') % (
                       tid, pou, gpart, bstat)
    elif state == 'state_exit':
        gpart = ('；门控含%s' % gsum) if gsum else ''
        if bstat == '已规范化-直接后继':
            boundary = ('状态退出(NOT)观察，源码迁移%s（POU=%s）%s；边界规范化=%s。'
                        '沿源码支持的迁移取首次离开退出集合的状态，不跨过出口找更远汇合点；'
                        '局部状态模型转换，不证明采样时间戳/路径活性/机械完成') % (tid, pou, gpart, bstat)
        else:
            boundary = ('状态退出(NOT)观察，源码迁移%s（POU=%s）%s；边界规范化=%s。'
                        '后继证据不完整：%s。全局退出等价未证明') % (
                           tid, pou, gpart, bstat, bnote[:80])
    elif state == 'adjacency_hypothesis':
        boundary = ('observed_states邻接观察（数字相邻），无源码迁移边；边界规范化=%s。'
                    '数字邻接不证明程序控制流迁移') % bstat
    elif state == 'reset_hypothesis':
        boundary = ('复位假设（→0），observed_states邻接观察；边界规范化=%s。'
                    '复位出口不自动当正常完成，保留返回安全位等生产动作') % bstat
    else:
        boundary = '状态=%s；边界规范化=%s；%s' % (state, bstat, bnote[:80])

    # ---- 工艺依据与过程类型按工序类别 ----
    if cat == 'weld':
        metal = '铜' if '铜焊' in ce else '铝'
        gpart = ('；门控含%s' % gsum) if gsum else ''
        if state == 'source_supported_transition':
            craft = '源码迁移%s（POU=%s）%s；%s焊接工序段，含焊头进退、夹紧与焊接保持等阶段' % (
                tid, pou, gpart, metal)
        else:
            craft = '%s焊接工序段（POU=%s）；%s焊接含焊头进退、夹紧与焊接保持等阶段，具体每步机械动作待人工核验' % (
                metal, pou, metal)
        ptype = '加工保持'
        obj = '%s焊接工序段（%s）本次状态阶段' % (metal, ce)
    elif cat == 'glue':
        gpart = ('；门控含%s' % gsum) if gsum else ''
        if state == 'source_supported_transition':
            craft = '源码迁移%s（POU=%s）%s；贴胶工序段（拉胶/备胶/贴胶/检测）' % (tid, pou, gpart)
        else:
            craft = '贴胶工序段（POU=%s）；含拉胶、备胶、贴胶及贴胶后检测等阶段' % pou
        ptype = '机械动作'
        obj = '贴胶工序段（%s）本次状态阶段' % ce
    elif cat == 'scan':
        gpart = ('；门控含%s' % gsum) if gsum else ''
        craft = '源码%s（POU=%s）%s；条码扫码数据交接' % (
            ('迁移'+tid) if state == 'source_supported_transition' else '观察', pou, gpart)
        ptype = '交接数据'
        obj = '条码扫码工序段（%s）本次状态阶段' % ce
    elif cat == 'inspect':
        gpart = ('；门控含%s' % gsum) if gsum else ''
        craft = '源码%s（POU=%s）%s；检测测量（焊印/蓝胶/转接片检测或抛NG）' % (
            ('迁移'+tid) if state == 'source_supported_transition' else '观察', pou, gpart)
        ptype = '检测测量'
        obj = '检测工序段（%s）本次状态阶段' % ce
    elif cat == 'docking':
        gpart = ('；门控含%s' % gsum) if gsum else ''
        craft = '源码%s（POU=%s）%s；接驳侧物料交接' % (
            ('迁移'+tid) if state == 'source_supported_transition' else '观察', pou, gpart)
        ptype = '交接数据'
        obj = '接驳交接工序段（%s）本次状态阶段' % ce
    elif cat == 'transport':
        gpart = ('；门控含%s' % gsum) if gsum else ''
        if state == 'source_supported_transition':
            craft = '源码迁移%s（POU=%s）%s；物料搬运（上下盘/移栽/横移/夹爪/蜘蛛手/转运/上片/下料）' % (
                tid, pou, gpart)
        else:
            craft = '物料搬运工序段（POU=%s）；含上下盘、移栽、横移、夹取、转运、下料等机械动作' % pou
        ptype = '机械动作'
        obj = '物料搬运工序段（%s）本次状态阶段' % ce
    else:
        gpart = ('；门控含%s' % gsum) if gsum else ''
        craft = '源码%s（POU=%s）%s；工序段状态阶段' % (
            ('迁移'+tid) if state == 'source_supported_transition' else '观察', pou, gpart)
        ptype = '机械动作'
        obj = '工序段（%s）本次状态阶段' % ce

    start_meaning = '%s进入%s（%s状态阶段开始）' % (ctrl, r['开始标签'], seg)
    if state == 'reset_hypothesis':
        end_meaning = '%s写入0（复位/返回安全位，状态阶段结束）' % ctrl
    elif 'NOT' in r['结束标签']:
        end_meaning = '%s离开%s（状态退出，后继见边界依据）' % (ctrl, r['原始开始标签'])
    else:
        end_meaning = '%s进入%s（状态阶段结束/下一阶段开始）' % (ctrl, r['结束标签'])

    note = '候选%s[%s]；%s' % (r['候选ID'], state, bnote[:60] if bnote else '边界规范化=' + bstat)
    return F(ptype, obj, start_meaning, end_meaning, craft, boundary, note)


# ---------------- 事件 reason ----------------
def event_reason(e, controller):
    cmd = e.get('command', '') or ''
    cmt = (e.get('comment', '') or '').strip()[:60]
    arg = (e.get('arguments', '') or '')[:40]
    if cmd in ('coil', 'coil_write', 'coil_set', 'coil_reset'):
        base = 'LD网络线圈动作观察（%s %s），guard为空且无状态锚点，起止标签无法由候选核验' % (cmd, arg)
    elif cmd == 'EXECUTE':
        base = '功能块EXECUTE调用观察（%s），无状态门控锚点，起止标签无法由候选核验' % arg
    elif cmd in ('LT', 'GT', 'GE', 'LE'):
        base = '比较运算观察（%s %s），无状态门控锚点，起止标签无法由候选核验' % (cmd, arg)
    elif cmd in ('CylinderPredictiveMaintenance', '伺服预测性维护监控',
                 'FB_AxisAlarm', 'FB_AxisOpreationManual', 'FB_AxisControl', 'FB_Cylinder',
                 'FB_SaftyDoorCtrl', 'Sequence_Time_FB', 'FB_MaterialEnable', 'FB_VacControl',
                 'FB_SingleiTSAutoGo', 'Op_EM_FB', 'FB_0305', 'FB_DrumMotorCotrol',
                 'FB_RMctrl', 'FB_轴组手动控制', 'FB_BarCodeScanCtrl', 'FB_ActDescription',
                 'FB_轴组控制三轴', 'FB_DoubleAxisGroupControl', 'FB_DrumLineCtrl',
                 'FB_AnalogQuantityIN', 'FB_AnalogQuantityOut', 'FB_VoltaeCurrent',
                 'FB_RfidWriteMoverID', 'FB_ST_Time', 'iTS_stn_control', 'iTS_sys_control',
                 'StartRangeCfgBegin_FC', 'SummationInternalTransition_FC',
                 'PMLTransitionCmdArry_ResetAllCmdSetAllSC_FC', 'MachineFB_M2MInteractive2',
                 'ConfigurePackML_ModeState_FC', 'PackML_OP_FB', 'TOM_EventsManagement_FB',
                 'PrChectout_Station_full', 'OEE_FB', 'ParetoAnalysis_FB',
                 'EventSearch_FC', 'CmdAndSC_Check_FC', 'EM_TransitionSummation_FC',
                 'HMI_Confirm_FB', 'HMI_State_FC', 'SummationEventStatus_FC',
                 'PMLTransitionCmd_SummarizePackTagCtrlCmd_FC', 'UN_PackML_Transmit_FB',
                 'PackMLModeStateMachine_FB', 'PackMLModeStateTimer_FB',
                 'ConfigurePackML_ModeStateMachine_FC', 'TowerSignalLights'):
        base = '功能块/函数调用观察（%s），无状态门控锚点，起止标签无法由候选核验' % cmd
    elif cmd in ('+', '-', 'XOR', 'MOD', 'ABS', 'LIMIT'):
        base = '算术/逻辑运算观察（%s），无状态门控锚点' % cmd
    elif cmd.startswith('MC_') or cmd == 'MC':
        base = '运动控制功能块调用观察（%s），调用网络无状态门控锚点' % cmd
    elif cmd in ('CTU', 'CTU_DINT', 'Inc', 'Up', 'Dec', 'BLINK', 'R_TRIG', 'PLS', 'Timer',
                 'Timer100ms', 'GetClk_100ms', 'GetClk_1min', 'GetTime', 'HeartBeat', 'HEARTBEAT',
                 'AccumulationTimer'):
        base = '计数/边沿/时钟脉冲观察（%s），非工艺过程，无状态门控边界' % cmd
    elif cmd in ('SktClose', 'SktTCPServer', 'SktTCPGetStatus', 'SktTCPSend', 'skttcprecv',
                 'SktTCPClearBuffer', 'SktTCPClient', 'SktUDPCreate', 'SktUDPSend', 'SktUDPRecv',
                 'B12_TCP客户端', 'LIMD_TCPClient', 'SysHC_NTPClient'):
        base = 'TCP/UDP通讯套接字调用观察（%s），无状态门控锚点' % cmd
    elif cmd in ('AryToString', 'StringToAry', 'ArySearch', 'RIGHT', 'LEN', 'CONCAT', 'DELETE',
                 'INT_TO_STRING', 'STRING_TO_INT', 'STRING_TO_USINT', 'DATE_TO_STRING',
                 'DT_ToDINTarray_FC', 'TimeToSec', 'REAL_TO_TIME', 'LINT_TO_REAL', 'LREAL_TO_REAL',
                 'UINT_TO_REAL', 'DINT_TO_REAL', 'LINT_TO_TIME', 'INT_TO_TIME', 'UINT_TO_TIME',
                 'NanoSecToTime', 'LINT_TO_INT', 'DINT_TO_LREAL', 'DINT_TO_DWORD', 'DWORD_TO_WORD',
                 'WORD_TO_UINT', 'WORD_TO_INT', 'UINT_TO_WORD', 'UINT_TO_INT', 'USINT_TO_DINT',
                 'USINT_TO_INT', 'USINT_TO_BYTE', 'BYTE_TO_USINT', 'INT_TO_WORD',
                 'TIME_TO_INT', 'MULTIME', 'DIVTIME', 'ModReal', 'SysHC_GetSystemDate',
                 'DT_TO_DATE'):
        base = '数据转换/字符串/运算指令观察（%s），无状态门控边界' % cmd
    elif cmd in ('Reset', 'ETC_GetMasterInfo', 'ETC_GetSlaveInfo'):
        base = '诊断/复位调用观察（%s），无状态门控边界' % cmd
    elif cmd == 'cross_fb_transport':
        base = '跨FB输送事件观察（%s），guard=%s' % (arg, e.get('guard', '')[:40])
    elif cmd == 'ST_assignment':
        base = 'ST赋值观察（%s），guard无本批候选状态锚点' % arg
    elif cmd == '动子控制':
        base = '动子控制调用观察，无状态门控锚点'
    elif cmd in ('PLC_PC_Communication', 'PPM_Distribution'):
        base = '功能块调用观察（%s），无状态门控锚点' % cmd
    else:
        base = '动作观察（%s %s），无状态门控锚点，起止标签无法由候选核验' % (cmd, arg)
    if cmt:
        base += '；源码注释：' + cmt
    return base + '。需先建立带状态锚点的候选方可转为采集区间'


# ---------------- main ----------------
man = json.loads((RUN / 'workflow_manifest.json').read_text(encoding='utf-8'))
cmap = {}
with (RUN / 'indexed_candidates.csv').open(encoding='utf-8-sig', newline='') as f:
    for r in csv.DictReader(f):
        cmap[r['候选ID']] = r
emap = {}
with (RUN / 'indexed_events.csv').open(encoding='utf-8-sig', newline='') as f:
    for r in csv.DictReader(f):
        emap[r['事件ID']] = r

# 全局 guard->候选开始标签索引（跨批锚定事件用）
start_index = {}
for cid, r in cmap.items():
    start_index.setdefault(norm(r['开始标签']), []).append(cid)

steps_total = 0
unresolved_total = 0
batch_summary = []
for b in man['batches']:
    d = json.loads((RUN / b['input']).read_text(encoding='utf-8'))
    steps = []
    unresolved = []
    used_events = set()
    batch_event_ids = {ev['事件ID'] for ev in d['events']}
    # 候选 steps
    for c in d['candidates']:
        f6 = classify(c)
        # 锚定事件：guard匹配候选开始标签 且 next_state匹配结束标签（若有）
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
        # 跨批锚定事件互引
        xrefs = []
        for cid_x in start_index.get(norm(c['开始标签']), []):
            if cid_x == c['候选ID']:
                continue
        steps.append({
            '构成要素': c['构成要素'], '动作': c['动作'], '动作描述': c['动作描述'],
            '开始标签': c['开始标签'], '结束标签': c['结束标签'],
            '核验状态': 'candidate',
            '证据候选ID': [c['候选ID']],
            '证据事件ID': attach,
            '过程类型': f6['过程类型'], '统计对象': f6['统计对象'],
            '起点含义': f6['起点含义'], '终点含义': f6['终点含义'],
            '工艺依据': f6['工艺依据'], '边界依据': f6['边界依据'],
            '说明': note,
        })
    # 剩余事件 -> unresolved
    for ev in d['events']:
        if ev['事件ID'] not in used_events:
            reason = event_reason(ev, b['controller'])
            g = norm(ev.get('guard', ''))
            if g and g in start_index:
                reason = reason + '；该事件guard与本批外候选开始标签一致：候选' + \
                         ','.join(start_index[g][:5]) + '（见其所在批次记录）'
            unresolved.append({'event_id': ev['事件ID'], 'reason': reason})
    result = {
        'batch_id': d['batch_id'],
        'steps': steps,
        'unresolved_events': unresolved,
        'notes': ('分类基于源码POU/迁移ID/门控变量/状态观察；候选边界规范化状态多为待核验或邻接假设，'
                  '无全局退出等价证明；主候选池未删减；事件按无状态锚点如实进入unresolved_events。'
                  '焊接段归加工保持、贴胶/搬运归机械动作、扫码归交接数据、检测归检测测量、'
                  '跨FB输送归工艺等待（含输送+互锁+等待焊接准备，非纯运动时长）。')
                 if steps else '本批无候选，仅有无状态锚点的动作观察，全部按协议进入unresolved_events。',
    }
    (RUN / b['output']).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    steps_total += len(steps)
    unresolved_total += len(unresolved)
    batch_summary.append((b['batch_id'], b['controller'], len(steps), len(unresolved)))

print('batches:', len(man['batches']), '| steps:', steps_total, '| unresolved:', unresolved_total)
# 输出每批摘要到文件
out = ['batch_id,controller,steps,unresolved']
for bid, ctrl, ns, nu in batch_summary:
    out.append('%s,%s,%d,%d' % (bid, ctrl, ns, nu))
Path(r'e:/users/yaoyuhui/AppData/Local/Temp/opencode/batch_summary_us.csv').write_text(
    chr(10).join(out), encoding='utf-8')
print('summary written to batch_summary_us.csv')
