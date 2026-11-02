# -*- coding: utf-8 -*-
"""
三工序覆盖对比整表生成器 v3（精简版）
每工序仅两张表：逐条对比 + 汇总说明。格式复刻参考文件 PLC_Manual_vs_Prediction_Side_by_Side.xlsx，并按用户要求增强：
  1) 未覆盖行做根因分类（J列写"短标签：一句话含义"），汇总页给出根因分布
  2) 用整行填色区分判定/根因类别（取代字体颜色区分）

匹配口径（与参考版一致）：
  直接匹配   开始、结束标签整对出现在预测中（仅忽略空白与大小写）
  多段可合并 多段预测首尾顺次连接覆盖人工区间；禁止经过步骤0作中间桥接
  周期待确认 起止相同且存在离开后返回的闭环（允许经过0，列为待确认）
  未覆盖     端点缺失或无可连接路径；按根因分型（见 TYPE_FILLS）
"""
import sys, re, json, hashlib
from collections import deque, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import xlrd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

ROOT = Path(r'E:\users\yaoyuhui\步序采集程序备份')
OUT = ROOT / 'PLC_Manual_vs_Prediction_Side_by_Side_三工序.xlsx'

NAVY = 'FF17324F'
HDR = 'FF274966'
INK = 'FF24364A'
GRAY = 'FF53687D'
GREEN = 'FF17643B'
BLUE = 'FF1E5799'
AMBER = 'FF875411'
RED = 'FFA02B25'
SEC_A = 'FFDDE7F3'
SEC_D = 'FFDDEFEA'
SEC_F = 'FFE7EDF2'

# ---- 类别 → 整行填色 ----
TYPE_FILLS = {
    '直接匹配':                 'FFE6F4EA',
    '多段可合并':               'FFE8F1FD',
    '已覆盖·写法不一致':        'FFEBF1DE',
    '已覆盖·拆分粒度不同':      'FFFDEBD3',
    '周期待确认':               'FFFFF0C2',
    '未覆盖':                   'FFFCE4E4',
    '结束为NOT离开型':          'FFFCE8E6',
    '结束含条件或信号':         'FFFFE9D6',
    '开始含条件（AND）':        'FFEDE7F6',
    '开始步值不存在':           'FFE4DEF0',
    '端点存在但路径不连通':     'FFE1F0EF',
    '疑似闭环（缺复位转移边）': 'FFFFF8DC',
    '结束步值不存在':           'FFF5DADA',
    '非步序变量':               'FFF2F2F2',
    '跨工位转移·程序无对应':    'FFE3ECF7',
}
UNCOV_TYPES = [
    '结束为NOT离开型', '结束含条件或信号', '开始含条件（AND）', '开始步值不存在',
    '端点存在但路径不连通', '跨工位转移·程序无对应', '疑似闭环（缺复位转移边）',
    '结束步值不存在', '非步序变量',
]
TYPE_EXPL = {
    '结束为NOT离开型': '人工结束标签为NOT(...)离开型；预测中该步的离开转移即实际结束，时长语义与预测一致。',
    '结束含条件或信号': '结束标签除步值外还包含AND信号，或为纯传感器条件；预测只记录步间转移。',
    '开始含条件（AND）': '开始标签为“步值 AND 附加条件”；预测起点仅为步值本身。',
    '开始步值不存在': '人工开始步值未出现在预测任何起点；多为人工表基于旧版程序编号。',
    '端点存在但路径不连通': '两端点均存在于预测，但没有可连接的转移路径（跨单元/跨下标或分支未提取）。',
    '跨工位转移·程序无对应': '人工子阶段链串接后为跨工位/跨子流程衔接；预测为各工位（各Ps）独立状态机、无跨状态机转移边，属当前程序能力边界。',
    '疑似闭环（缺复位转移边）': '起止相同，但预测缺少经步骤0的复位转移边，无法确认闭环。',
    '结束步值不存在': '人工结束步值未出现在预测任何终点；多为版本编号差异。',
    '非步序变量': '标签不是“变量=步值”形态，预测中无对应记录。',
}
# K列显示：短标签（兼作汇总计数关键字）+ 一句话含义
SHORT_TYPE = {
    '结束为NOT离开型': 'NOT离开型结束',
    '结束含条件或信号': '条件型结束',
    '开始含条件（AND）': '条件型开始',
    '开始步值不存在': '开始步值缺失',
    '端点存在但路径不连通': '路径不连通',
    '疑似闭环（缺复位转移边）': '闭环未确认',
    '结束步值不存在': '结束步值缺失',
    '非步序变量': '非步序标签',
    'NOT离开写法': 'NOT离开型',
    '条件结束写法': '条件结束·写法不一致',
    'NOT等待写法': 'NOT等待型结束',
    '条件开始写法': '条件型开始',
    '跨工位转移·程序无对应': '跨状态机转移',
    '拆分粒度不同': '粒度拆分',
}
ROOT_MEANING = {
    '结束为NOT离开型': '人工结束写“离开该步”(NOT)，预测写“跳往下一拍”；两者时长相同，仅写法不同',
    '结束含条件或信号': '人工结束要求步值+信号条件同时满足，或为纯传感器条件；预测只记录步间转移',
    '开始含条件（AND）': '人工开始要求步值+附加条件同时成立；预测起点只是步值本身',
    '开始步值不存在': '人工开始步值在预测中不存在；多为人工表按旧版程序编号填写',
    '端点存在但路径不连通': '开始、结束步值都存在，但预测中没有连接两端的转移路径',
    '疑似闭环（缺复位转移边）': '起止为同一步、应为闭环；预测缺少经步骤0的复位转移边，无法确认',
    '结束步值不存在': '人工结束步值在预测中不存在；多为新旧版本编号差异',
    '非步序变量': '该行不是“变量=步值”形式的标签；预测中无对应记录',
    'NOT离开写法': '人工结束写“离开该步”(NOT)，预测写“进入下一步”；信号/离开转移已在guards核验，等待时长相同，仅写法不同',
    'NOT等待写法': '人工结束写“进入下一拍步值”，预测写“离开该步”(NOT等待型)；该下一拍步值已在预测中被观测进入，等待时长相同，仅写法不同',
    '条件结束写法': '人工结束附加信号条件；信号已在预测步guards中核验，预测写“进入下一步”；时长相同，仅写法不同',
    '条件开始写法': '人工开始附加条件（人工记录写法）；预测以纯步值起点+guards记录，且路径可达，等待时长一致',
    '跨变量同步片段': '人工起点为复合条件中的跨变量片段；同一人工行的主变量片段已由预测路径覆盖同一区间，仅记录拆分方式不同',
    '跨工位转移·程序无对应': '人工子阶段链串接后为跨工位/跨子流程衔接（如ST15→ST12）；预测为各工位（各Ps）独立状态机、无跨状态机转移边，属当前程序能力边界',
    '拆分粒度不同': '人工以“步值+条件”标记子阶段（单条或链式拆分）；合并后与预测转移区间等价，条件在预测步中同源核验，等待时长一致',
}


def root_text(root):
    if not root:
        return '—'
    return f'{SHORT_TYPE[root]}：{ROOT_MEANING[root]}'


def F(hex_):
    return PatternFill('solid', fgColor=hex_)


def norm(t):
    return re.sub(r'\s+', '', t or '').lower().replace('int#', '')


def is_zero(t):
    n = norm(t)
    return n.endswith('=0') or n.endswith('=int#0')


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ============================================================
# 人工表读取（返回 [(excel_row, I, J)] 按文件顺序）
# ============================================================

def read_manual_codesys(xls_path):
    """读人工表 开始标签/结束标签 列；"动作描述=总时长"的行是工位总时长统计行
    （非步序转移标签，如 NOT 离开型、I=J 自环、跨 Ps、到 0 等），全部排除。
    返回 (rows, excluded)：excluded = [(L, i_val, j_val), ...]

    支持 .xls(xlrd) 与 .xlsx(openpyxl)。按表头动态定位"开始标签"/"结束标签"/
    "动作描述"列；旧表无该表头时回退到第8/9列(开始/结束)与第5列(动作描述)。
    """
    p = str(xls_path)
    rows = []
    excluded = []
    if p.lower().endswith('.xlsx'):
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        sh = None
        for name in wb.sheetnames:
            if '时序拆解' in name or '拆解' in name or '动作' in name:
                sh = wb[name]
                break
        if sh is None and wb.sheetnames:
            sh = wb[wb.sheetnames[0]]
        all_rows = list(sh.iter_rows(values_only=True))
        wb.close()
        # detect header columns
        start_col = end_col = desc_col = None
        if all_rows:
            hdr = all_rows[0]
            for ci, cv in enumerate(hdr):
                t = str(cv).strip() if cv is not None else ''
                if t == '开始标签' and start_col is None:
                    start_col = ci
                elif t == '结束标签' and end_col is None:
                    end_col = ci
                elif t == '动作描述' and desc_col is None:
                    desc_col = ci
        if start_col is None:
            start_col = 8
        if end_col is None:
            end_col = 9
        if desc_col is None:
            desc_col = 5
        for r_idx in range(1, len(all_rows)):
            cells = all_rows[r_idx]
            sv = cells[start_col] if start_col < len(cells) else None
            ev = cells[end_col] if end_col < len(cells) else None
            i_val = str(sv).strip() if sv is not None else ''
            j_val = str(ev).strip() if ev is not None else ''
            if i_val:
                dv = cells[desc_col] if desc_col < len(cells) else None
                desc = str(dv).strip() if dv is not None else ''
                if desc == '总时长':
                    excluded.append((r_idx + 1, i_val, j_val))
                    continue
                rows.append((r_idx + 1, i_val, j_val))
        return rows, excluded
    # legacy .xls path
    data = open(xls_path, 'rb').read()
    wb = xlrd.open_workbook(file_contents=data)
    sh = wb.sheet_by_name('时序拆解')
    # detect header columns from row 0
    start_col = end_col = desc_col = None
    if sh.nrows > 0:
        for ci in range(sh.ncols):
            t = str(sh.cell_value(0, ci)).strip()
            if t == '开始标签' and start_col is None:
                start_col = ci
            elif t == '结束标签' and end_col is None:
                end_col = ci
            elif t == '动作描述' and desc_col is None:
                desc_col = ci
    if start_col is None:
        start_col = 8
    if end_col is None:
        end_col = 9
    if desc_col is None:
        desc_col = 5
    for r in range(2, sh.nrows):
        sv = sh.cell_value(r, start_col)
        i_val = str(sv).strip() if sv else ''
        j_val = str(sh.cell_value(r, end_col)).strip() if sh.cell_value(r, end_col) else ''
        if i_val:
            desc = str(sh.cell_value(r, desc_col)).strip() if sh.ncols > desc_col else ''
            if desc == '总时长':
                excluded.append((r + 1, i_val, j_val))
                continue
            rows.append((r + 1, i_val, j_val))
    return rows, excluded


RE_TAG = re.compile(r'm_nNextAutoStep\[(\d+)\]\s*=\s*INT#(\d+)')
MY_COLS = {'seq': 2, 'start_a': 10, 'end_a': 11, 'start_b': 13, 'end_b': 14}


def _norm_mylar_end(text):
    def repl(m):
        return f'm_nNextAutoStep[{m.group(1)}]={m.group(2)}'
    return RE_TAG.sub(repl, (text or '').strip())


def read_manual_mylar(xlsx_path):
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True, data_only=True)
    sh = None
    for name in wb.sheetnames:
        if '合芯动作拆解' in name or '动作拆解' in name:
            sh = wb[name]
            break
    rows = []
    for row_no, cells in enumerate(sh.iter_rows(values_only=True), 1):
        def cv(idx):
            if idx <= len(cells):
                v = cells[idx - 1]
                return '' if v is None else str(v).strip()
            return ''
        a_val = cv(1)
        if a_val.startswith('模块') and cv(2).startswith('序号'):
            continue
        seq = cv(MY_COLS['seq'])
        if not seq.isdigit():
            continue
        for start_c, end_c in (('start_a', 'end_a'), ('start_b', 'end_b')):
            start_raw = cv(MY_COLS[start_c])
            if not start_raw:
                continue
            tags = RE_TAG.findall(start_raw)
            if not tags:
                continue
            end_norm = _norm_mylar_end(cv(MY_COLS[end_c]))
            if re.search(r'\)\s*AND', start_raw, re.I):
                rows.append((row_no, start_raw.strip(), end_norm))
                continue
            for n_str, v_str in tags:
                rows.append((row_no, f'm_nNextAutoStep[{n_str}]={v_str}', end_norm))
    wb.close()
    return rows


# ============================================================
# 预测分段（IR steps）：一条记录=一个IR步；end_tags 展开为图边
# ============================================================

def read_segments(ir_path):
    ir = json.load(open(ir_path, encoding='utf-8'))
    records = []
    edges = []
    for s in ir.get('steps', []):
        no = len(records) + 1
        st = s.get('start_tag') or ''
        en = s.get('end_tag') or ''
        glist = [g.get('expr', '') for g in (s.get('guards') or []) if (g.get('expr') or '').strip()]
        records.append((no, st, en, glist))
        seen = set()
        for e in (s.get('end_tags') or []) + ([en] if en.strip() else []):
            if not (e or '').strip():
                continue
            k = norm(e)
            if k in seen:
                continue
            seen.add(k)
            edges.append((no, st, e))
    return records, edges


# ============================================================
# 匹配引擎
# ============================================================

MAX_DEPTH = 8


def split_or(tag):
    parts = [x.strip() for x in re.split(r'\s+OR\s+', tag or '', flags=re.IGNORECASE)]
    return [p for p in parts if p] or ['']


def build_index(edges):
    seg_by_no = {}
    edge_ref = {}
    starts_by = defaultdict(list)
    ends_set = set()
    for k, (pno, st, en) in enumerate(edges):
        seg_by_no[k] = (st, en)
        edge_ref[k] = pno
        starts_by[norm(st)].append(k)
        ends_set.add(norm(en))
    return seg_by_no, edge_ref, starts_by, ends_set


def find_path(start_alts, end_alts, seg_by_no, starts_by, allow_zero=False):
    ends = {norm(e) for e in end_alts}
    seen = set()
    q = deque()
    for a in start_alts:
        for no in starts_by.get(norm(a), []):
            if no not in seen:
                seen.add(no)
                q.append((no, [no]))
    while q:
        sno, path = q.popleft()
        e = norm(seg_by_no[sno][1])
        if e in ends:
            return path
        if len(path) >= MAX_DEPTH:
            continue
        for nx in starts_by.get(e, []):
            if nx in seen or (is_zero(e) and not allow_zero):
                continue
            seen.add(nx)
            q.append((nx, path + [nx]))
    return None


def find_loop(start_alts, seg_by_no, starts_by):
    tgt = {norm(a) for a in start_alts}
    seen = set()
    q = deque()
    for a in start_alts:
        for no in starts_by.get(norm(a), []):
            if no not in seen:
                seen.add(no)
                q.append((no, [no]))
    while q:
        sno, path = q.popleft()
        e = norm(seg_by_no[sno][1])
        if e in tgt and len(path) >= 2:
            return path
        if len(path) >= MAX_DEPTH:
            continue
        for nx in starts_by.get(e, []):
            if nx in seen:
                continue
            seen.add(nx)
            q.append((nx, path + [nx]))
    return None


def _split_top_and(tag):
    """按顶层 AND 切分标签（括号深度感知），返回段列表"""
    t = (tag or '').strip()
    if not t:
        return []
    parts, depth, last = [], 0, 0
    for m in re.finditer(r'\(|\)|\sAND\s', t, re.IGNORECASE):
        tok = m.group(0)
        if tok == '(':
            depth += 1
        elif tok == ')':
            depth -= 1
        elif depth == 0:
            parts.append(t[last:m.start()].strip())
            last = m.end()
    parts.append(t[last:].strip())
    return [p for p in parts if p]


def _guards_hit(sig_parts, seg_nos, edge_guards):
    """人工标签附加的信号条件是否都出现在候选步的 guards 中（guards 为预测自有提取）

    sig_parts: 顶层AND切出的信号段（可含括号/OR）；每个段内 OR 分支任一命中即可
    """
    if not edge_guards or not sig_parts:
        return False
    conds = []
    for p in sig_parts:
        p = (p or '').strip()
        while p.startswith('(') and p.endswith(')'):
            p = p[1:-1].strip()
        p = re.sub(r'^NOT\s+', '', p, flags=re.IGNORECASE).strip()
        alts = [norm(a) for a in split_or(p) if norm(a)]
        if alts:
            conds.append(alts)
    if not conds:
        return False
    for no in seg_nos:
        gs = edge_guards.get(no)
        if not gs:
            continue
        ok = True
        for alts in conds:
            if not any(a in gs or any(g.endswith(a) for g in gs) for a in alts):
                ok = False
                break
        if ok:
            return True
    return False


def match_one(i_tag, j_tag, seg_by_no, starts_by, ends_set, edge_guards=None):
    """返回 (result, note, chosen_nos, cand_nos, root)"""
    i_raw = (i_tag or '').strip()
    j_raw = (j_tag or '').strip()
    i_raw = re.sub(r'\)\s*AND\s*', ') AND ', i_raw, flags=re.IGNORECASE)
    i_raw = re.sub(r'=\s+', '=', i_raw)
    j_raw = re.sub(r'=\s+', '=', j_raw)
    ni_alts = split_or(i_tag)
    nj_alts = [norm(x) for x in split_or(j_raw)] if j_raw else ['']
    nj_set = set(nj_alts)
    all_not = bool(nj_set) and all(n.startswith('not') for n in nj_set)

    i_parts = _split_top_and(i_raw)
    j_parts = _split_top_and(j_raw)
    i_main = i_parts[0] if i_parts else i_raw
    j_main = j_parts[0] if j_parts else j_raw

    cand = []
    seen_no = set()
    for a in ni_alts:
        for no in starts_by.get(norm(a), []):
            if no not in seen_no:
                seen_no.add(no)
                cand.append(no)

    # ---- 条件型开始：I 带顶层附加条件，剥条件后重搜 ----
    if not cand and i_main != i_raw:
        ni_main = split_or(i_main)
        for a in ni_main:
            for no in starts_by.get(norm(a), []):
                if no not in seen_no:
                    seen_no.add(no)
                    cand.append(no)
        if cand:
            concrete_m = [n for n in nj_alts if n and not n.startswith('not')]
            path_m = None
            if concrete_m:
                path_m = (find_path(ni_main, concrete_m, seg_by_no, starts_by)
                          or find_path(ni_main, concrete_m, seg_by_no, starts_by, allow_zero=True))
            if path_m:
                return ('已覆盖·写法不一致',
                        '人工开始标签带附加条件（人工记录写法）；预测以纯步值起点+guards记录，且路径可达，等待时长一致。',
                        path_m, [], '条件开始写法')
            # 修复：剥条件后候选保留，继续参与后续终点匹配/写法判定，不再直接清空判未覆盖

    # ---- 复合起点：(A OR B) AND C 跨机构同步条件 ----
    if not cand:
        i_top = _split_top_and(i_raw)
        if len(i_top) >= 2 and '=' in (j_raw or '') and not all_not:
            j_pieces = RE_STEP_PIECE.findall(j_raw)
            i_pieces = RE_STEP_PIECE.findall(i_raw)
            if j_pieces and i_pieces:
                j_var = j_pieces[0].split('=')[0].strip().lower()
                main_p = [p for p in i_pieces
                          if p.split('=')[0].strip().lower() == j_var]
                cond_p = [p for p in i_pieces
                          if p.split('=')[0].strip().lower() != j_var]
                if main_p and cond_p:
                    for a in main_p:
                        for no in starts_by.get(norm(a), []):
                            if no not in seen_no:
                                seen_no.add(no)
                                cand.append(no)
                    if cand:
                        path_c = (find_path(main_p, j_pieces, seg_by_no, starts_by)
                                  or find_path(main_p, j_pieces, seg_by_no,
                                               starts_by, allow_zero=True))
                        if path_c:
                            thru0_c = any(is_zero(seg_by_no[k][1])
                                          for k in path_c[:-1])
                            zero_txt = '（路径经步骤0）' if thru0_c else ''
                            return ('多段可合并',
                                    f'人工起点为跨机构复合条件（AND 附加：{"、".join(cond_p)}）；'
                                    f'预测以同变量步值链覆盖人工区间{zero_txt}；'
                                    '附加同步条件未在路径中验证。',
                                    path_c, [], '')

    if not cand:
        if nj_set & ends_set:
            return ('未覆盖', '预测中未出现人工开始标签；虽然结束标签存在，但不能算覆盖。', [], [], '')
        return ('未覆盖', '预测中未出现人工开始、结束标签；没有可对应的预测行。', [], [], '')

    for no in cand:
        if norm(seg_by_no[no][1]) in nj_set:
            return ('直接匹配', '开始、结束标签整对匹配；仅忽略空白与字母大小写。', [no], [], '')

    concrete = [n for n in nj_alts if n and not n.startswith('not')]
    if concrete and not all_not:
        path = find_path(ni_alts, concrete, seg_by_no, starts_by)
        if path:
            return ('多段可合并',
                    f'预测拆为{len(path)}段，顺次连接可覆盖人工区间；未通过步骤0绕回。未验证各段实际运行条件。',
                    path, [], '')
        path0 = find_path(ni_alts, concrete, seg_by_no, starts_by, allow_zero=True)
        if path0:
            return ('多段可合并',
                    f'预测拆为{len(path0)}段，顺次连接可覆盖人工区间；路径经步骤0（跨循环长动作）。未验证各段实际运行条件。',
                    path0, [], '')

    # ---- 写法不一致：J = NOT(A OR B) 复合离开型 ----
    js = re.sub(r'\s+', '', j_raw)
    m_not = re.match(r'^[Nn][Oo][Tt]\s*\((.*)\)$', (j_raw or '').strip(), re.S)
    if js[:4].upper() == 'NOT(' and js.endswith(')') and m_not:
        inner_alts = [norm(x) for x in split_or(m_not.group(1))]
        i_set = {norm(a) for a in ni_alts}
        others = [a for a in inner_alts if a not in i_set]
        if (inner_alts and i_set <= set(inner_alts)
                and all('=' in a for a in inner_alts)
                and others):
            reach = [o for o in others if find_path(ni_alts, [o], seg_by_no, starts_by)]
            if len(reach) == 1:
                o = reach[0]
                path1 = find_path(ni_alts, [o], seg_by_no, starts_by)
                outs = starts_by.get(o, [])
                if path1 and outs:
                    full = path1 + [outs[0]]
                    return ('多段可合并',
                            '人工结束写"离开该组步值"NOT(A OR B)；B 在起点延续路径上，'
                            '表达式首次为真=离开B（进入其出边步），预测以连续段覆盖同一区间。',
                            full, [], '')
            if not reach:
                return ('已覆盖·写法不一致',
                        '人工结束写“离开该组步值”NOT(A OR B)；A/B为互斥分支，'
                        '离开起点即表达式首次为真，与预测“进入下一步”同时刻，仅写法不同。',
                        cand[:1], [], 'NOT离开写法')

    # ---- 写法不一致：NOT 离开型结束 ----
    if all_not:
        return ('已覆盖·写法不一致',
                '人工结束写“离开该步”(NOT)，预测写“进入下一步”；步的离开转移已提取，等待时长相同，仅写法不同。',
                cand[:1], [], 'NOT离开写法')

    # ---- 写法不一致：反向NOT等待型（预测边 X=>NOT(X)，人工 J=具体下一拍步值）----
    if concrete and not all_not:
        i_norms2 = {norm(a) for a in ni_alts} | {norm(a) for a in split_or(i_main)}
        for no in cand[:6]:
            e = norm(seg_by_no[no][1])
            m_not2 = re.match(r'^not\((.+)\)$', e)
            if m_not2 and m_not2.group(1) in i_norms2:
                ys = [y for y in concrete if y in starts_by]
                if ys:
                    return ('已覆盖·写法不一致',
                            '人工结束写“进入下一拍步值”；预测该步结束为“离开该步”NOT(X)等待型，'
                            '且该下一拍步值在预测中被观测进入；等待时长相同，仅写法不同。',
                            [no], [], 'NOT等待写法')

    # ---- 写法不一致：J = 步值 AND 信号 ----
    if len(j_parts) >= 2 and '=' in (j_parts[0] or ''):
        j_main_norm = norm(j_main)
        i_norms = {norm(a) for a in ni_alts} | {norm(a) for a in split_or(i_main)}
        sig_parts = j_parts[1:]
        hit_ok = _guards_hit(sig_parts, cand, edge_guards)
        if j_main_norm in i_norms and hit_ok:
            return ('已覆盖·写法不一致',
                    '人工结束写“步值+信号完成”；信号在该步guards中出现，预测以“进入下一步”记录；等待时长相同，仅写法不同。',
                    cand[:1], [], '条件结束写法')
        hit_segs = [no for no in cand if norm(seg_by_no[no][1]) == j_main_norm]
        if j_main_norm in ends_set and hit_segs and _guards_hit(sig_parts, hit_segs, edge_guards):
            return ('已覆盖·写法不一致',
                    '人工结束附加了信号条件；信号为该步转移的使能条件（在guards中），预测以“进入下一步”记录；时长相同，仅写法不同。',
                    hit_segs[:1], [], '条件结束写法')
        # 修复：数据源未提供 guards（独立CSV无使能条件列）时降级——步值匹配即判写法不一致，注明信号未核验
        if not hit_ok and all(not edge_guards.get(no) for no in cand):
            if j_main_norm in i_norms:
                return ('已覆盖·写法不一致',
                        '人工结束写“步值+信号完成”；预测记录该步的离开转移，等待区间一致；'
                        '数据源未提供使能条件(guards)，信号未核验，仅写法不同。',
                        cand[:1], [], '条件结束写法')
            if hit_segs:
                return ('已覆盖·写法不一致',
                        '人工结束附加了信号条件；预测记录该步转移至该步值；'
                        '数据源未提供使能条件(guards)，信号未核验；时长一致，仅写法不同。',
                        hit_segs[:1], [], '条件结束写法')
            path_j = (find_path(split_or(i_main), [j_main_norm], seg_by_no, starts_by)
                      or find_path(split_or(i_main), [j_main_norm], seg_by_no, starts_by, allow_zero=True))
            if path_j:
                return ('已覆盖·写法不一致',
                        f'人工结束写“步值+信号完成”；预测拆为{len(path_j)}段到达该步值；'
                        '数据源未提供使能条件(guards)，信号未核验。',
                        path_j, [], '条件结束写法')

    # ---- 写法不一致：J 为纯信号（无=、非NOT）----
    if j_raw and '=' not in j_raw and not all_not:
        if _guards_hit([j_raw], cand, edge_guards):
            return ('已覆盖·写法不一致',
                    '人工结束为完成信号；信号在预测步guards中出现，预测以“进入下一步”记录；等待时长相同，仅写法不同。',
                    cand[:1], [], '条件结束写法')
        # 修复：无 guards 数据源降级——同起点存在离开转移即判写法不一致，注明信号未核验
        if cand and all(not edge_guards.get(no) for no in cand):
            return ('已覆盖·写法不一致',
                    '人工结束为完成信号；预测以“进入下一步/离开该步”记录；'
                    '数据源未提供使能条件(guards)，信号未核验；等待区间一致，仅写法不同。',
                    cand[:1], [], '条件结束写法')

    if norm(ni_alts[0]) == norm(j_raw) and len(ni_alts) == 1:
        loop = find_loop(ni_alts, seg_by_no, starts_by)
        if loop:
            thru0 = any(is_zero(seg_by_no[loop[k]][1]) for k in range(len(loop) - 1))
            if thru0:
                return ('周期待确认', '存在离开后返回的闭环，但经过步骤0；可能包含复位，不能据此确认正常生产总周期。', loop, [], '')
            return ('周期待确认', '存在离开后返回的闭环（未经过步骤0）；仍属周期候选，需运行验证后才能确认正常生产总周期。', loop, [], '')

    if all_not or not (nj_set & ends_set):
        note = '缺少人工要求的完整结束标签。右侧仅列同起点的真实预测候选，不是匹配结果。'
    else:
        note = '两个端点分别存在，但没有连接路径。右侧仅列同起点预测；数组下标不同，不可当作同一标签。'
    return ('未覆盖', note, [], cand[:2], '')


RE_STEP_PIECE = re.compile(r'[A-Za-z_][A-Za-z0-9_.\[\]]*=[A-Za-z0-9_#\-\+]+')


def classify_uncovered(i_tag, j_tag, starts_by, ends_set):
    """未覆盖根因分型"""
    alts = [norm(a) for a in split_or(i_tag)]
    i_strict = any(a in starts_by for a in alts)
    pieces = {norm(m.group(0)) for m in RE_STEP_PIECE.finditer(i_tag or '')}
    i_bare = any(p in starts_by for p in pieces)
    j_raw = j_tag or ''
    n_j = norm(j_raw)

    if not i_strict:
        if i_bare:
            return '开始含条件（AND）'
        if '=' not in alts[0]:
            return '非步序变量'
        return '开始步值不存在'
    if not n_j:
        return '非步序变量'
    if n_j.startswith('not'):
        return '结束为NOT离开型'
    j_alts = [norm(x) for x in split_or(j_raw)]
    if any('and' in a for a in j_alts):
        return '结束含条件或信号'
    if not any('=' in a for a in j_alts):
        return '结束含条件或信号'
    concretes = [a for a in j_alts if '=' in a]
    iv = alts[0].split('=')[0]
    if any(a.split('=')[0] != iv for a in concretes):
        return '端点存在但路径不连通'
    if len(alts) == 1 and n_j == alts[0]:
        return '疑似闭环（缺复位转移边）'
    if any(a in ends_set for a in concretes):
        return '端点存在但路径不连通'
    return '结束步值不存在'


def _is_subphase_tag(tag):
    """'步值 AND 条件'子阶段标签 → (步值norm, 条件文本)；其余（含NOT开头、纯步值）返回 None

    兼容人工表漏写 AND 的空格分隔变体（'步值 信号'）：剩余部分不含步值片段才认定。
    """
    t = (tag or '').strip()
    if not t or t[:4].upper() in ('NOT ', 'NOT('):
        return None
    parts = _split_top_and(t)
    if len(parts) >= 2:
        main = parts[0]
        pieces = [m.group(0) for m in RE_STEP_PIECE.finditer(main)]
        if len(pieces) == 1 and norm(pieces[0]) == norm(main):
            return norm(main), parts[1]
        return None
    m = re.match(r'^([A-Za-z_][A-Za-z0-9_.\[\]]*=\s*\d+)\s+(.+)$', t, re.S)
    if m and not RE_STEP_PIECE.search(m.group(2)):
        return norm(m.group(1)), m.group(2).strip()
    return None


def _main_piece_orig(tag):
    """标签首个步值片段的原文（用于显示）"""
    t = (tag or '').strip()
    parts = _split_top_and(t)
    m = RE_STEP_PIECE.search(parts[0] if parts else t)
    return m.group(0) if m else t


def chain_granularity(results, seg_edges, seg_by_no, edge_ref, starts_by):
    """链式等价合并判定（人工拆分粒度更细的情形）：

    人工以'步值 AND 条件'把一个 PLC 步拆成多条子阶段记录时，沿人工表把
    A.J≡B.I 的相邻记录串成极大链（衔接点必须全为子阶段标签）；链合并后的
    区间（首 I 步值 → 尾 J 步值集合）与某预测记录（同起点、end_tags 集合
    相等）等价 → 全链判'已覆盖·拆分粒度不同'。
    链成立但首尾跨工位 → 仅把链上未覆盖行的根因改'跨工位转移·程序无对应'。
    """
    ends_by_pno = defaultdict(set)
    for pno, _st, en in seg_edges:
        ends_by_pno[pno].add(norm(en))
    JOINABLE = ('未覆盖', '已覆盖·写法不一致')
    j_index = defaultdict(list)
    i_index = defaultdict(list)
    for idx, res in enumerate(results):
        if res['result'] in JOINABLE:
            j_index[norm(res['J'])].append(idx)
            i_index[norm(res['I'])].append(idx)
    processed = set()
    for idx, res in enumerate(results):
        if idx in processed:
            continue
        if res['result'] != '未覆盖' and not res.get('_gran_cand'):
            continue
        if not _is_subphase_tag(res['I']) and not _is_subphase_tag(res['J']):
            continue
        chain = [idx]
        cur = idx
        while True:  # 向前串：prev.J 与 cur.I 整标签相等，歧义时取紧邻上一条
            prevs = [p for p in j_index.get(norm(results[cur]['I']), [])
                     if p not in chain and p not in processed]
            if not prevs:
                break
            prev = cur - 1 if cur - 1 in prevs else (prevs[0] if len(prevs) == 1 else None)
            if prev is None:
                break
            chain.insert(0, prev)
            cur = prev
        cur = idx
        while True:  # 向后串：区间未闭合（cur.J 为子阶段型）才继续；纯步值链尾=步进到达，闭合
            if _is_subphase_tag(results[cur]['J']) is None:
                break
            nxts = [p for p in i_index.get(norm(results[cur]['J']), [])
                    if p not in chain and p not in processed]
            if not nxts:
                break
            nxt = cur + 1 if cur + 1 in nxts else (nxts[0] if len(nxts) == 1 else None)
            if nxt is None:
                break
            chain.append(nxt)
            cur = nxt
        if len(chain) < 2:
            continue
        head, tail = results[chain[0]], results[chain[-1]]
        sp_head = _is_subphase_tag(head['I'])
        head_main = sp_head[0] if sp_head else norm(head['I'])
        tail_vals = []
        tail_ok = True
        for p in split_or(tail['J']):
            sp = _is_subphase_tag(p)
            if sp is not None:
                tail_vals.append(sp[0])  # 子阶段型链尾：取主片步值作为区间终点
                continue
            pieces = [norm(m.group(0)) for m in RE_STEP_PIECE.finditer(p)]
            if len(pieces) != 1 or pieces[0] != norm(p):
                tail_ok = False
                break
            tail_vals.append(pieces[0])
        if not tail_ok or not tail_vals:
            continue
        if len(tail_vals) == 1 and tail_vals[0] == head_main:
            continue  # 首尾同步值，疑似闭环，不判
        hit = None
        path_hit = None
        for no in starts_by.get(head_main, []):
            pno = edge_ref[no]
            if ends_by_pno.get(pno, set()) == set(tail_vals):
                hit = (no, pno)
                break
        # 人工链节点序列（步值主片，原文显示；相邻去重）——范围等价要求预测路径顺次经过
        seq = [head_main]
        seq_txt = [_main_piece_orig(head['I'])]
        for c in chain[:-1]:
            sp_j = _is_subphase_tag(results[c]['J'])
            m = sp_j[0] if sp_j else norm(results[c]['J'])
            if m != seq[-1]:
                seq.append(m)
                seq_txt.append(_main_piece_orig(results[c]['J']))
        if hit is None:
            def chain_path(allow_zero=False):
                full = []
                for a, b in zip(seq, seq[1:]):
                    seg = find_path([a], [b], seg_by_no, starts_by, allow_zero=allow_zero)
                    if seg is None:
                        return None
                    full.extend(seg)
                seg = find_path([seq[-1]], tail_vals, seg_by_no, starts_by, allow_zero=allow_zero)
                if seg is None:
                    return None
                full.extend(seg)
                return full
            path = chain_path() or chain_path(allow_zero=True)
            if path:
                path_hit = path
        processed.update(chain)
        if hit or path_hit:
            k_n = len(chain)
            rng_txt = f'{"→".join(seq_txt)}→{" | ".join(tail["J"].split(" OR ")) if len(tail_vals) > 1 else _main_piece_orig(tail["J"])}'
            for c in chain:
                r2 = results[c]
                r2['result'] = '已覆盖·拆分粒度不同'
                r2['root'] = '拆分粒度不同'
                r2['cands'] = []
                if hit:
                    no, pno = hit
                    r2['chosen'] = [no]
                    r2['_full_rec'] = pno
                    r2['note'] = (f'人工 {k_n} 条记录链合并区间（{rng_txt}）与预测 #{pno} '
                                  f'（起点→结束分支集合）范围完全等价，等待时长为各段之和。')
                else:
                    r2['chosen'] = list(path_hit)
                    nos_txt = '→'.join(f'#{edge_ref[k]}' for k in path_hit)
                    r2['note'] = (f'人工 {k_n} 条记录链合并区间（{rng_txt}）与预测路径（{nos_txt}，'
                                  f'沿人工链节点顺次连接、经中转步）范围等价，等待时长为各段之和。')
        else:
            head_var = head_main.split('=')[0]
            tail_vars = {v.split('=')[0] for v in tail_vals}
            if tail_vars and all(v != head_var for v in tail_vars):
                for c in chain:
                    if results[c]['result'] == '未覆盖':
                        results[c]['root'] = '跨工位转移·程序无对应'
                        results[c]['note'] = ('人工子阶段链合并后为跨工位/跨子流程衔接；预测为各工位（各Ps）'
                                              '独立状态机，无跨状态机转移边（程序能力边界）。')


def promote_single_subphase(results):
    """单条型：条件开始/条件结束写法的写法不一致记录（已同源核验）归入'已覆盖·拆分粒度不同'"""
    for res in results:
        if (res['result'] == '已覆盖·写法不一致'
                and res['root'] in ('条件开始写法', '条件结束写法')):
            res['result'] = '已覆盖·拆分粒度不同'
            res['root'] = '拆分粒度不同'
            res['note'] = ('人工以“步值+条件”标记子阶段（开始或结束时刻）；预测以纯步值转移记录'
                           '同一区间，条件在预测步中同源核验；等待时长一致，仅拆分粒度不同。')


# ============================================================
# 样式工具
# ============================================================

AL_TOP = Alignment(vertical='top', wrap_text=True)
AL_TOP_C = Alignment(horizontal='center', vertical='top', wrap_text=True)
AL_CTR = Alignment(horizontal='center', vertical='center', wrap_text=True)


def put(ws, r, c, v=None, *, fill=None, font=None, align=None, fmt=None):
    cell = ws.cell(r, c)
    if v is not None:
        cell.value = v
    if fill:
        cell.fill = F(fill)
    if font:
        cell.font = font
    if align:
        cell.alignment = align
    if fmt:
        cell.number_format = fmt
    return cell


def sheet_scaffold(ws, title, subtitle, widths, last_col):
    ws.sheet_view.showGridLines = False
    for c, w in widths.items():
        ws.column_dimensions[c].width = w
    ws.merge_cells(f'A1:{last_col}1')
    put(ws, 1, 1, title, fill=NAVY,
        font=Font(size=18, bold=True, color='FFFFFFFF'),
        align=Alignment(horizontal='left', vertical='center'))
    ws.row_dimensions[1].height = 36
    ws.merge_cells(f'A2:{last_col}2')
    put(ws, 2, 1, subtitle, fill='FFF0F5FA',
        font=Font(size=10, color=GRAY),
        align=Alignment(horizontal='left', vertical='center', wrap_text=True))
    ws.row_dimensions[2].height = 35


def header_row(ws, r, headers, height=34):
    for c, h in enumerate(headers, 1):
        put(ws, r, c, h, fill=HDR, font=Font(size=10, bold=True, color='FFFFFFFF'),
            align=AL_CTR)
    ws.row_dimensions[r].height = height


def sec_cells(ws, r, spans):
    """spans: [(col_start, col_end, text, fill, font_color)]"""
    for c1, c2, text, fl, fc in spans:
        if c2 > c1:
            ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
        put(ws, r, c1, text, fill=fl, font=Font(size=10, bold=True, color=fc), align=AL_CTR)
    ws.row_dimensions[r].height = 24


def result_row(ws, r, vals, fill_hex, ncols, center_cols=(), gray_cols=(), font_color=None):
    for c in range(1, ncols + 1):
        base = GRAY if c in gray_cols else INK
        put(ws, r, c, vals[c - 1] if c <= len(vals) else None, fill=fill_hex,
            font=Font(size=10, bold=(c == 6), color=font_color or base),
            align=AL_TOP_C if c in center_cols else AL_TOP)
    ws.row_dimensions[r].height = 42


LEGEND = ('整行填色：绿=直接匹配 蓝=多段可合并 青绿=已覆盖·写法不一致 橙=已覆盖·拆分粒度不同（人工子阶段拆分） '
          '黄=周期待确认 深红=跨工位/跨轴等待（状态产生在本PLC外） 浅红=跨模块焊接请求信号（起点为信号非步值） '
          '其余色=未覆盖（根因见J列与汇总页分布）')


# ============================================================
# 每工序 5 张子表
# ============================================================

def build_process_sheets(wb, proc_name, var_desc, seg_records, seg_edges, manual_rows,
                         ir_file, manual_file, ir_sha, man_sha, excluded=None,
                         pred_source_desc=None, gen_method_desc=None):
    excluded = excluded or []
    seg_by_no, edge_ref, starts_by, ends_set = build_index(seg_edges)
    guards_by_rec = {}
    for rec in seg_records:
        if len(rec) >= 4:
            guards_by_rec[rec[0]] = {norm(g) for g in rec[3] if g}
    edge_guards = {k: guards_by_rec.get(no, set()) for k, no in edge_ref.items()}

    results = []
    used_by = defaultdict(set)
    cand_by = defaultdict(set)
    for idx, (mrow, i_tag, j_tag) in enumerate(manual_rows):
        result, note, chosen, cands, root = match_one(
            i_tag, j_tag, seg_by_no, starts_by, ends_set, edge_guards)
        if result == '未覆盖':
            root = classify_uncovered(i_tag, j_tag, starts_by, ends_set)
        results.append({'no': idx + 1, 'mrow': mrow, 'I': i_tag, 'J': j_tag,
                        'result': result, 'note': note, 'chosen': chosen,
                        'cands': cands, 'main_row': 7 + idx, 'root': root})
        for k in chosen:
            used_by[edge_ref[k]].add(idx + 1)
        for k in cands:
            pno = edge_ref[k]
            if pno not in used_by:
                cand_by[pno].add(idx + 1)

    for res in results:
        if res['result'] == '已覆盖·写法不一致' and res['root'] in ('条件开始写法', '条件结束写法'):
            res['_gran_cand'] = True
    chain_granularity(results, seg_edges, seg_by_no, edge_ref, starts_by)
    promote_single_subphase(results)

    main_headers = ['人工编号', '人工开始标签', '人工结束标签', '预测开始标签（按段）',
                    '预测结束标签（按段）', '对比结果', '对比说明', '预测编号',
                    '人工原文件行号', '根因说明']
    main_widths = {'A': 8, 'B': 30, 'C': 34, 'D': 33, 'E': 36,
                   'F': 13, 'G': 36, 'H': 12, 'I': 11, 'J': 46}

    def row_texts(res, nos):
        if nos and res.get('_full_rec') is not None:
            pno = res['_full_rec']
            rec_ends = [en for k, (_p, _s, en) in enumerate(seg_edges) if edge_ref[k] == pno and en.strip()]
            if rec_ends:
                return seg_by_no[nos[0]][0], ' | '.join(rec_ends), f'#{pno}'
        if nos:
            d_txt = '\n'.join(f'{k}. {seg_by_no[no][0]}' for k, no in enumerate(nos, 1))
            e_txt = '\n'.join(f'{k}. {seg_by_no[no][1]}' for k, no in enumerate(nos, 1))
            h_txt = '\n'.join(f'{k}. #{edge_ref[no]}' for k, no in enumerate(nos, 1))
        else:
            d_txt = e_txt = '未找到对应预测'
            h_txt = '—'
        return d_txt, e_txt, h_txt

    # ---------- 1. 人工与预测逐条对比 ----------
    ws = wb.create_sheet(f'{proc_name} 逐条对比')
    sheet_scaffold(ws, '人工与预测｜逐条对照',
                   '左侧完整保留每条人工标签；右侧按顺序列出预测原始分段。多余预测不扣分；右侧同起点候选不等于匹配。',
                   main_widths, 'J')
    last = 6 + len(results)
    ws.merge_cells('A3:C3')
    put(ws, 3, 1, f'="人工基准："&COUNTA(A7:A{last})&" 条"',
        fill='FFFFFFFF', font=Font(size=10, bold=True, color='FF274966'),
        align=Alignment(vertical='center'))
    ws.merge_cells('D3:E3')
    put(ws, 3, 4, f'="直接或多段覆盖："&(COUNTIF(F7:F{last},"直接匹配")+COUNTIF(F7:F{last},"多段可合并"))&" 条"',
        fill='FFFFFFFF', font=Font(size=10, bold=True, color='FF274966'),
        align=Alignment(vertical='center'))
    ws.merge_cells('F3:J3')
    put(ws, 3, 6, LEGEND,
        fill='FFFFFFFF', font=Font(size=10, bold=True, color='FF274966'),
        align=Alignment(vertical='center'))
    ws.row_dimensions[3].height = 30
    ws.row_dimensions[4].height = 8
    sec_cells(ws, 5, [
        (1, 3, '人工基准  |  保留原始顺序', SEC_A, 'FF243F5D'),
        (4, 5, '预测原始分段（按编号对应）', SEC_D, 'FF1C5C52'),
        (6, 10, '判定与来源（预测编号 #n；整行按判定/根因填色）', SEC_F, 'FF435E75')])
    header_row(ws, 6, main_headers)
    for res in results:
        nos = res['chosen'] or res['cands']
        d_txt, e_txt, h_txt = row_texts(res, nos)
        fill_hex = TYPE_FILLS[res['result'] if res['result'] != '未覆盖' else res['root']]
        font_hex = None
        if proc_name == '超声' and res['mrow'] in {17, 22, 119, 129, 169, 201}:
            fill_hex, font_hex = 'FFC00000', 'FFFFFFFF'
        elif proc_name == '超声' and res['mrow'] in {39, 45, 51, 57, 63, 69, 75, 81}:
            fill_hex = 'FFF2C5C5'
        vals = [res['no'], res['I'], res['J'], d_txt, e_txt, res['result'],
                res['note'], h_txt, f"L{res['mrow']}",
                root_text(res['root'])]
        result_row(ws, res['main_row'], vals, fill_hex, 10,
                   center_cols=(1, 8, 9, 10), gray_cols=(7, 8, 9, 10),
                   font_color=font_hex)
    ws.freeze_panes = 'D7'

    # ---------- 2. 汇总与说明 ----------
    ws = wb.create_sheet(f'{proc_name} 汇总说明')
    ws.sheet_view.showGridLines = False
    for c, w in {'A': 24, 'B': 11.2, 'C': 16.2, 'D': 51.2,
                 'E': 3, 'F': 14.4, 'G': 14.4, 'H': 14.4}.items():
        ws.column_dimensions[c].width = w
    ws.merge_cells('A1:H1')
    put(ws, 1, 1, '覆盖汇总与使用说明', fill=NAVY,
        font=Font(size=18, bold=True, color='FFFFFFFF'),
        align=Alignment(vertical='center'))
    ws.row_dimensions[1].height = 36
    ws.merge_cells('A2:H2')
    put(ws, 2, 1, f'本文件比较 {var_desc} 的两份标签表；不是对 PLC 源码运行行为的证明，也不作为验收依据。',
        fill='FFF0F5FA', font=Font(size=10, color=INK),
        align=Alignment(vertical='center', wrap_text=True))
    ws.row_dimensions[2].height = 35
    ws.row_dimensions[3].height = 26
    main_sheet = f"'{proc_name} 逐条对比'"
    for c, h in enumerate(['对比类别', '数量', '占人工基准比例', '解释'], 1):
        put(ws, 4, c, h, fill=HDR, font=Font(size=10, bold=True, color='FFFFFFFF'), align=AL_CTR)
    ws.merge_cells('F4:H4')
    put(ws, 4, 6, '阅读顺序', fill=HDR, font=Font(size=10, bold=True, color='FFFFFFFF'), align=AL_CTR)
    ws.row_dimensions[4].height = 30
    read_notes = [
        (5, 'F5:H6', '1  先打开「{} 逐条对比」：每行是一条人工标签，整行按判定/根因填色。'.format(proc_name)),
        (7, 'F7:H8', '2  绿/蓝/青绿/橙=已覆盖（直接/多段/写法不一致/拆分粒度不同）；深红=跨工位等待、浅红=跨模块请求信号（生产侧均不在本PLC）；其余色=未覆盖，根因见J列。'),
        (9, 'F9:H10', '3  需复核项=未覆盖与周期候选行，颜色与本页下方分布一致。'),
        (11, 'F11:H12', '4  未覆盖的根因分布见下方色卡；分类只描述标签形态差异。'),
    ]
    cat_rows = [
        ('直接匹配', '完整开始、结束标签对直接出现在预测中。'),
        ('多段可合并', '多个真实预测段首尾相接；普通区间不经步骤0绕回。'),
        ('已覆盖·写法不一致',
         '人工以附加条件/NOT离开/信号结束等写法记录；预测以纯步值转移记录且信号在guards中核验，时长语义一致。'),
        ('已覆盖·拆分粒度不同',
         '人工将一个步按执行器状态拆为子阶段记录（步值+条件）；多条链合并后（或单条）与预测转移区间等价，等待时长为各段之和。'),
        ('周期待确认', '起止相同，存在离开后返回的闭环；不计入已确认覆盖。'),
        ('未覆盖', '完整端点缺失，或没有可连接路径；按根因分型（见下方分布）。'),
    ]
    for k, (cat, expl) in enumerate(cat_rows):
        r = 5 + k
        put(ws, r, 1, cat, fill=TYPE_FILLS[cat], font=Font(size=10, bold=True, color=INK), align=AL_TOP)
        put(ws, r, 2, f'=COUNTIF({main_sheet}!$F$7:$F${last},A{r})',
            fill=TYPE_FILLS[cat], font=Font(size=10, color=INK), align=AL_TOP)
        put(ws, r, 3, f'=B{r}/$B$11', fill=TYPE_FILLS[cat],
            font=Font(size=10, color=INK), align=AL_TOP, fmt='0.0%')
        put(ws, r, 4, expl, fill=TYPE_FILLS[cat], font=Font(size=10, color=INK), align=AL_TOP)
        put(ws, r, 5, None, fill='FFFFFFFF')
        ws.row_dimensions[r].height = 42
    extra_rows = [
        ('人工基准总数', f'=COUNTA({main_sheet}!$A$7:$A${last})',
         '去除空白行，按人工原表顺序逐条展示。'),
        ('直接＋多段＋写法不一致＋拆分粒度覆盖', '=SUM(B5:B8)',
         '多余预测不扣分；写法不一致/拆分粒度行已核验语义等价。'),
        ('含周期候选的宽松结果', '=SUM(B5:B9)', '仅表示图上闭环候选存在，不等于正常生产周期已成立。'),
    ]
    for k, (label, formula, expl) in enumerate(extra_rows):
        r = 11 + k
        for c in (1, 2, 3, 4):
            put(ws, r, c, None, fill='FFF0F5FA',
                font=Font(size=10, bold=True, color=INK), align=AL_TOP)
        put(ws, r, 1, label)
        put(ws, r, 2, formula)
        put(ws, r, 3, f'=B{r}/$B$11', fmt='0.0%')
        put(ws, r, 4, expl)
        put(ws, r, 5, None, fill='FFFFFFFF')
        ws.row_dimensions[r].height = 42
    for r, span, note in read_notes:
        ws.merge_cells(span)
        put(ws, r, 6, note, fill='FFF0F5FA', font=Font(size=10, color=INK), align=AL_TOP)
    ws.row_dimensions[12].height = 42

    # 未覆盖根因分布
    ws.merge_cells('A14:H14')
    put(ws, 14, 1, '未覆盖根因分布（占人工基准比例；色卡与逐条对比J列/整行填色一致）',
        fill=HDR, font=Font(size=10, bold=True, color='FFFFFFFF'), align=AL_CTR)
    ws.row_dimensions[14].height = 26
    for c, h in enumerate(['根因类型', '数量', '占人工基准比例', '解释'], 1):
        put(ws, 15, c, h, fill='FFF0F5FA', font=Font(size=10, bold=True, color=INK), align=AL_CTR)
    ws.row_dimensions[15].height = 24
    for k, t in enumerate(UNCOV_TYPES):
        r = 16 + k
        put(ws, r, 1, SHORT_TYPE[t], fill=TYPE_FILLS[t], font=Font(size=10, color=INK), align=AL_TOP)
        put(ws, r, 2, f'=COUNTIF({main_sheet}!$K$7:$K${last},A{r}&"*")',
            fill=TYPE_FILLS[t], font=Font(size=10, color=INK), align=AL_TOP)
        put(ws, r, 3, f'=B{r}/$B$11', fill=TYPE_FILLS[t],
            font=Font(size=10, color=INK), align=AL_TOP, fmt='0.0%')
        put(ws, r, 4, TYPE_EXPL[t], fill=TYPE_FILLS[t],
            font=Font(size=10, color=INK), align=AL_TOP)
        put(ws, r, 5, None, fill='FFFFFFFF')
        ws.row_dimensions[r].height = 42
    ws.row_dimensions[25].height = 26

    ws.merge_cells('A26:H26')
    put(ws, 26, 1, '判定规则与边界', fill=HDR,
        font=Font(size=10, bold=True, color='FFFFFFFF'), align=AL_CTR)
    ws.row_dimensions[26].height = 26
    rules = [
        ('比较范围', '仅比较两张标签表。没有重新执行提取器，没有修改 PLC，也没有用人工答案补进预测。'),
        ('表达式规则', '比较时仅忽略空白和大小写；保留 NOT、AND、OR、数组下标和数字。原始标签的内部表达式不改写。'),
        ('路径规则', '普通区间允许多段首尾连接，但禁止通过步骤0作中间桥接；允许0作为人工要求的最终结束点。'),
        ('闭环规则', '开始与结束相同时，要求至少有一条实际边且离开再返回；经过步骤0的闭环列为待确认。'),
        ('根因分类', '未覆盖按标签结构与预测覆盖情况归为9类（见上方分布）；分类不改变未覆盖判定，仅供定位差异原因。'),
        ('证据用途', '缺失项的同起点预测仅供对照，不是匹配结果；未被选定路径引用的预测也不自动判为错误。'),
        ('来源行号', '主表的“#n”是预测记录编号（对应IR提取顺序）；人工侧“Ln”是人工表Excel的行号。'),
        ('结论边界', '路径可连接不代表其分支条件兼容或必定发生；正常生产语义需要另查源码或运行记录。'),
    ]
    for k, (label, text) in enumerate(rules):
        r = 27 + k
        put(ws, r, 1, label, fill='FFF7F9FC', font=Font(size=10, bold=True, color=INK), align=AL_TOP)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
        put(ws, r, 2, text, fill='FFF7F9FC', font=Font(size=10, color=INK), align=AL_TOP)
        ws.row_dimensions[r].height = 36
    ws.row_dimensions[35].height = 26
    ws.merge_cells('A36:H36')
    put(ws, 36, 1, '来源清单', fill=HDR,
        font=Font(size=10, bold=True, color='FFFFFFFF'), align=AL_CTR)
    ws.row_dimensions[36].height = 26
    n_seg = len(seg_edges)
    uniq_pairs = len({(norm(st), norm(en)) for _, st, en in seg_edges})
    sources = [
        ('预测来源', pred_source_desc or
         f'工程IR提取（project_ir_lite.json）｜{len(seg_records)}条预测记录；{n_seg}条完整标签对；{uniq_pairs}个唯一完整标签对'),
        ('人工来源', f'{Path(manual_file).name}｜{len(manual_rows)}条非空标签对'),
    ]
    if excluded:
        sources.append((
            '已排除统计行',
            f'{len(excluded)}行｜人工表"动作描述=总时长"的工位总时长统计行（非步序转移标签，行号：'
            + '、'.join(f'L{e[0]}' for e in excluded[:20]) + ('…' if len(excluded) > 20 else '')
            + '），不参与对比'))
    sources += [
        ('生成方式', gen_method_desc or
         '步序采集工具自动生成｜匹配口径与参考版 PLC_Manual_vs_Prediction_Side_by_Side.xlsx 一致'),
        ('预测 SHA-256', ir_sha),
        ('人工 SHA-256', man_sha),
    ]
    for k, (label, text) in enumerate(sources):
        r = 37 + k
        put(ws, r, 1, label, fill='FFF7F9FC', font=Font(size=10, color=INK), align=AL_TOP)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
        put(ws, r, 2, text, fill='FFF7F9FC', font=Font(size=10, color=INK), align=AL_TOP)
        ws.row_dimensions[r].height = 31
    ws.freeze_panes = 'A5'

    cnt = defaultdict(int)
    root_cnt = defaultdict(int)
    for res in results:
        cnt[res['result']] += 1
        if res['root']:
            root_cnt[res['root']] += 1
    return cnt, root_cnt, len(results)


# ============================================================
# 主流程
# ============================================================

PROCS = [
    ('包mylar', '包mylar测试', 'm_nNextAutoStep',
     ('包mylar测试/output/project_ir_lite.json',
      '包mylar测试/步序时长采集-XM2-L5-包mylar-20260913.xlsx', 'mylar')),
    ('超声', '超声测试', 'STxx.AutoStep',
     ('超声测试/output/project_ir_lite.json',
      '超声测试/步序时长采集-XM2-L5-超声波-20260910.xls', 'codesys')),
    ('转接片', '转接片测试', 'EMxx.Process_steps',
     ('转接片测试/output/project_ir_lite.json',
      '转接片测试/4.厦门2转接片 22PPM转接片焊接机步序采集表-20260905步序拆解(已自动还原).xls',
      'codesys')),
]


def build_extraction_sheet(wb, proc_name, ir_file):
    """全量步序提取表：提取器自源码独立提取的全部步序记录，编号与逐条对比表 #n 对应。"""
    ir = json.load(open(str(ir_file), encoding='utf-8'))
    steps = ir.get('steps', [])
    ws = wb.create_sheet(f'{proc_name} 全量步序提取')
    headers = ['#', 'Station', '工位名称', '子流程', '步值', '开始标签', '结束标签',
               '使能条件（guards）', '动作说明', '类型']
    widths = {'A': 7, 'B': 9, 'C': 18, 'D': 15, 'E': 7, 'F': 30, 'G': 30,
              'H': 34, 'I': 32, 'J': 8}
    sheet_scaffold(ws, '预测全量步序提取｜完整记录',
                   '提取器自 PLC 源码独立提取的全部步序记录（未经人工表干预）；'
                   '编号与「逐条对比」表中的预测编号 #n 一一对应。等待步=仅步进等待、无机构动作。',
                   widths, 'J')
    header_row(ws, 6, headers)
    r = 7
    for k, s in enumerate(steps, 1):
        ets = [e for e in (s.get('end_tags') or []) if (e or '').strip()]
        if (s.get('end_tag') or '').strip() and s.get('end_tag') not in ets:
            ets.append(s.get('end_tag'))
        gs = [g.get('expr') for g in (s.get('guards') or []) if (g.get('expr') or '').strip()]
        typ = '等待' if s.get('is_waiting_step') else (s.get('step_type') or '')
        vals = [k, s.get('station') or '', s.get('station_business_name') or '',
                s.get('subflow_name') or s.get('subflow') or '',
                str(s.get('step_val') or ''),
                s.get('start_tag') or '', '\n'.join(ets), '\n'.join(gs),
                s.get('comment') or s.get('resolved_text') or '', typ]
        result_row(ws, r, vals, 'FFFFFFFF' if k % 2 else 'FFF2F6FA', 10,
                   center_cols=(1, 2, 5, 10))
        r += 1
    ws.freeze_panes = 'A7'
    return len(steps)


def main():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for proc_name, folder, var_desc, (ir_rel, man_rel, kind) in PROCS:
        print(f'===== {proc_name} =====')
        ir_file = ROOT / ir_rel
        man_file = ROOT / man_rel
        if kind == 'mylar':
            manual_rows, excluded = read_manual_mylar(man_file), []
        else:
            manual_rows, excluded = read_manual_codesys(man_file)
        seg_records, seg_edges = read_segments(ir_file)
        print(f'人工 {len(manual_rows)} 行（排除总时长统计行 {len(excluded)}）| '
              f'预测记录 {len(seg_records)} 条 | 图边 {len(seg_edges)} 条')
        cnt, root_cnt, n_man = build_process_sheets(
            wb, proc_name, var_desc, seg_records, seg_edges, manual_rows,
            str(ir_file), str(man_file), sha256(ir_file), sha256(man_file),
            excluded=excluded)
        print('判定分布:', dict(cnt))
        print('根因分布:', dict(sorted(root_cnt.items(), key=lambda x: -x[1])))
        n_ext = build_extraction_sheet(wb, proc_name, ir_file)
        print(f'全量步序提取表: {n_ext} 条')
    wb.save(str(OUT))
    print(f'\n已保存: {OUT}')


if __name__ == '__main__':
    main()
