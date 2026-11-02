# -*- coding: utf-8 -*-
"""B-tier action description generator for 转接片 run.
Reads batches_desc/batch_*.input.json, produces batch_*.result.json.

Generates pure-Chinese process descriptions from kind + tokens.
No GT access. refs must be from the row's tokens. Chinese-only, no ASCII.
"""
import json, os, glob, re

DDIR = r'outputs\转接片\batches_desc'

# ---- Action token -> Chinese description mapping ----
# Maps token patterns to Chinese description fragments.
# All descriptions are pure Chinese (no ASCII identifiers).

def axis_desc(tok):
    """Servo axis movement description."""
    m = re.search(r'Axis_([XYZR])(\d?)', tok)
    ax = (m.group(1) if m else '?') + '轴'
    if 'Location_Auto' in tok or 'Location_Select' in tok or 'Location_Move' in tok:
        nm = re.search(r'\[(\d+)\]', tok)
        n = nm.group(1) if nm else ''
        return '%s定位到%s位' % (ax, n) if n else '%s定位' % ax
    if 'JOG_Auto' in tok:
        nm = re.search(r'\[(\d+)\]', tok)
        n = nm.group(1) if nm else ''
        return '%s点动到%s位' % (ax, n) if n else '%s点动' % ax
    if 'Stop_Auto' in tok:
        return '%s停止' % ax
    if 'Home_Auto' in tok:
        return '%s回零' % ax
    if 'ToPos' in tok:
        return '%s移动到目标位' % ax
    if 'AbsModel' in tok:
        return '%s绝对定位' % ax
    return '%s运动' % ax

def cyl_desc(tok):
    """Cylinder action description. Auto_V=extend, Auto_R=retract."""
    m = re.search(r'Cy_([A-Za-z0-9_]+?)(?:_\d+)?\.In', tok)
    name = m.group(1) if m else '气缸'
    name_map = {
        'Up': '上升', 'Location': '定位', 'Push': '推料', 'Clamp': '夹紧',
        'Press': '压紧', 'Lock': '锁紧', 'Block': '阻挡', 'Separate': '分离',
        'Open': '打开', 'Cut': '切割', 'Check': '检查', 'LaserCheckProtection': '激光防护',
        'WeldDustRemoval': '焊接除尘',
    }
    cn = name
    for k, v in name_map.items():
        if name.startswith(k):
            cn = v
            break
    if 'Auto_V' in tok or 'Auto_v' in tok:
        return '%s气缸伸出' % cn
    if 'Auto_R' in tok or 'Auto_r' in tok:
        return '%s气缸缩回' % cn
    return '%s气缸动作' % cn

def clamp_desc(tok):
    """Clamp action description."""
    if 'Clamp_Auto_V' in tok:
        return '夹具夹紧'
    if 'Clamp_Auto_R' in tok:
        return '夹具松开'
    if 'Press_Auto_R' in tok:
        return '夹具压紧复位'
    if 'Press_Auto_V' in tok:
        return '夹具压紧'
    return '夹具动作'

def vacuum_desc(tok):
    """Vacuum action description. Auto_V=vacuum on, Auto_R=release."""
    m = re.search(r'Va_([A-Za-z]+)', tok)
    name = m.group(1) if m else ''
    if name.startswith('Plate'):
        cn = '吸盘'
    elif name.startswith('Cover'):
        cn = '吸盖'
    elif name.startswith('Glue'):
        cn = '吸胶'
    else:
        cn = '真空'
    if 'Auto_V' in tok:
        return '%s吸附' % cn
    if 'Auto_R' in tok:
        return '%s破真空' % cn
    return '%s动作' % cn

def motor_desc(tok):
    """Motor movement description."""
    m = re.search(r'Mo_([A-Za-z]+)', tok)
    name = m.group(1) if m else '电机'
    if name.startswith('Load'):
        cn = '上料'
    elif name.startswith('UnLoad'):
        cn = '下料'
    elif name.startswith('Translation'):
        cn = '平移'
    else:
        cn = '电机'
    if 'StartRight' in tok:
        return '%s电机启动' % cn
    return '%s电机动作' % cn

def robot_desc(tok):
    """Robot movement description."""
    if 'ToPos' in tok:
        return '机器人移动到目标位'
    if 'ToEM02' in tok:
        return '机器人移动到工位'
    if 'ToEM05' in tok:
        return '机器人移动到工位'
    if 'ToEM06' in tok:
        return '夹具工作到位'
    if 'ToEM08' in tok:
        return '机器人移动到工位'
    if 'ToNG' in tok:
        return '机器人移动到不良品位'
    if 'ToTransfer' in tok:
        return '机器人移动到中转位'
    if 'ToX1' in tok:
        return '机器人移动到目标位'
    return '机器人动作'

def blow_desc(tok):
    """Blow operation description."""
    if 'Nitrogen' in tok:
        return '吹氮气执行'
    if 'GlueDispensing' in tok:
        return '吹胶执行'
    if 'SprayGlue' in tok:
        return '吹喷胶执行'
    return '吹气执行'

def signal_desc(tok):
    """Signal/flag description."""
    if 'LinkOut_Request' in tok:
        return '请求出料信号'
    if 'LinkOut_Done' in tok:
        return '出料完成信号'
    if 'Module_RunEndStop' in tok:
        return '模组运行完成信号'
    if 'WeldStart' in tok:
        return '启动焊接信号'
    if 'Focusing_WeldStart' in tok:
        return '对焦焊接启动信号'
    if 'SprayGlueStart' in tok:
        return '启动喷胶信号'
    if 'Done' in tok:
        return '完成信号'
    if 'Permit' in tok:
        return '允许信号'
    if 'StationDonotStop' in tok:
        return '工位不停信号'
    if 'AllowNGCover' in tok:
        return '允许不良品罩信号'
    if 'NGOut_Allow' in tok:
        return '允许不良品排出信号'
    if 'Complete' in tok:
        return '完成信号'
    if 'AxisHomingFlag' in tok:
        return '轴回零标志'
    if 'AxisMovingFlag' in tok:
        return '轴运动标志'
    if 'Clamp.*Work_ToEM' in tok or 'Work_ToEM' in tok:
        return '夹具工作到位信号'
    if 'CellLocationFinish' in tok:
        return '电芯定位完成信号'
    if 'WeldSealSupport' in tok:
        return '焊接密封支撑信号'
    if 'Golytec' in tok or 'StationMotion' in tok:
        return '工位运动到位'
    if 'User_Alarm' in tok:
        return '报警信号'
    if 'General_Warning' in tok:
        return '报警信号'
    if 'H_In' in tok:
        return '入料信号'
    return '信号确认'

def describe_token(tok, action):
    """Return Chinese description for a single action token."""
    if not tok or tok == 'nan':
        return ''
    # Servo positioning (generic label)
    if action == '伺服定位':
        return '伺服定位'
    if action == '置位信号':
        return signal_desc(tok)
    # Axis movements
    if 'Axis_' in tok:
        return axis_desc(tok)
    # Cylinder actions
    if tok.startswith('Cy_'):
        return cyl_desc(tok)
    # Clamp actions
    if tok.startswith('Clamp') or '.Clamp_' in tok:
        return clamp_desc(tok)
    # Vacuum actions
    if tok.startswith('Va_'):
        return vacuum_desc(tok)
    # Motor movements
    if tok.startswith('Mo_'):
        return motor_desc(tok)
    # Robot movements
    if tok.startswith('Robot') or 'Robot' in tok:
        return robot_desc(tok)
    # Blow operations
    if tok.startswith('Blow_'):
        return blow_desc(tok)
    # Welding signals
    if 'WeldStart' in tok or 'Focusing_WeldStart' in tok:
        return '启动焊接'
    if 'WeldSeal' in tok:
        return '焊接密封支撑'
    # Glue signals
    if 'SprayGlueStart' in tok:
        return '启动喷胶'
    # LinkOut signals
    if 'LinkOut' in tok:
        return signal_desc(tok)
    if 'Module_RunEndStop' in tok:
        return signal_desc(tok)
    # Done/Permit/Complete
    if 'Done' in tok or 'Permit' in tok or 'Complete' in tok:
        return signal_desc(tok)
    # Alarm
    if 'User_Alarm' in tok or 'Warning' in tok:
        return '报警信号'
    # Golytec station motion
    if 'Golytec' in tok or 'StationMotion' in tok:
        return '工位运动到位'
    # CellLocationFinish
    if 'CellLocationFinish' in tok:
        return '电芯定位完成信号'
    # H_In
    if tok.startswith('H_In'):
        return '入料信号'
    # Array index tokens like [6,16] [6,17]
    if re.match(r'^\[\d+', tok):
        return '步进过渡'
    # Fallback
    return ''


def make_desc(row):
    """Generate description for a row based on kind and tokens."""
    kind = row.get('kind', '')
    tokens = row.get('tokens', [])
    descs = []
    refs = []
    for t in tokens:
        tok = t.get('tok', '')
        if not tok or tok == 'nan':
            continue
        d = describe_token(tok, t.get('action', ''))
        if d and d not in descs:
            descs.append(d)
            refs.append(tok)
    if not descs:
        if kind == '步进':
            return '步进过渡', refs
        if kind == '信号/标志':
            return '信号确认', refs
        if kind == '机械动作':
            return '机构动作', refs
        if kind == '动作+信号':
            return '动作及信号收尾', refs
        return '步进过渡', refs
    # For step transitions, just use first meaningful action
    if kind == '步进':
        return '步进过渡', refs
    # For signal-only, combine
    if kind == '信号/标志':
        return '；'.join(descs[:2]), refs
    # For action+signal, combine action + signal
    if kind == '动作+信号':
        return '；'.join(descs[:2]), refs
    # For mechanical action, combine up to 2
    return '；'.join(descs[:2]), refs


def process_desc_batch(fp):
    with open(fp, encoding='utf-8') as f:
        d = json.load(f)
    descs = {}
    for row in d.get('rows', []):
        rid = str(row.get('id', ''))
        desc, refs = make_desc(row)
        descs[rid] = {
            'ok': True,
            'desc': desc,
            'refs': refs,
            'kind': row.get('kind', ''),
        }
    result = {'batch_id': d.get('batch_id', 0), 'descs': descs}
    out_path = fp.replace('.input.json', '.result.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return len(descs)


def main():
    files = sorted(glob.glob(os.path.join(DDIR, '*.input.json')))
    print('processing %d desc batches...' % len(files))
    total = 0
    for fp in files:
        n = process_desc_batch(fp)
        total += n
    print('done. total descriptions=%d' % total)


if __name__ == '__main__':
    main()
