# -*- coding: utf-8 -*-
"""Gen workstation-engineering xlsx from a final_prediction_pairs.csv.

Layout (matches the human collection workbook style):
  col B 工位   — merged cells per workstation (rows contiguous)
  col C 步序   — ACTION VARIABLES of the start state when available:
                 PLCopen:  plcopen_action_trace.csv targets by (state_variable, state_value)
                 omron  :  native_command_evidence.csv arguments by start == 开始标签
                 fallback: real transition <变量>: <起步>→<止步>; source-code notes
                 (*...*) appended when present. No invented descriptions.
  col D/E tags — 开始标签 / 结束标签 verbatim.

Usage:
  python gen_workstation_xlsx.py --prediction outputs/X/final_prediction_pairs.csv [--proc-name 工序名] [--group-by auto|构成要素|证据POU|控制器]
  [--out outputs/X/<工序名>_步序采集表.xlsx]  (缺省输出: prediction 同目录/<工序名>_步序采集表.xlsx, 最终交付文件命名)
  [--run outputs/X]  (directory containing the action-trace / command-evidence files)
The prediction file and evidence files are only read; never modified.

工位分组缺省auto：构成要素含SC04命名用构成要素，否则有证据POU列用证据POU；
也可显式指定 证据POU（取「区域::POU」的区域前缀）或 控制器。表头上方注明工序名、来源文件、行数、
SHA256戳与分组方式；排序仅为展示查找便利，不代表执行顺序或工艺路径。
步序/标签内容完全来自同一行预测记录与源码证据文件，无推断文本；
该xlsx仅供人阅读，评价仍直接读CSV。finish(workflow.validate)成功后自动生成本表。
"""
import argparse, hashlib, json, re, os
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side


def clean_doc(text: str) -> str:
    """Documentation may wrap a GrpComment XML fragment with embedded monitor
    JSON ( <Sub/>{k:...} ); keep only the plain zh name before <Sub/>."""
    t = str(text)
    m = re.search(r'<GrpComment_zh-cn>(.*?)</GrpComment_zh-cn>', t, re.S)
    if m:
        t = m.group(1)
    i = t.find('<Sub/>')
    if i >= 0:
        t = t[:i]
    t = re.sub(r'<[^>]+>', '', t)
    return re.sub(r'\s+', '', t).strip()


def load_var_docs(run: str):
    """variable root name -> cleaned Chinese documentation (source facts)."""
    docs = {}
    if run:
        p = os.path.join(run, 'variable_documentation.json')
        if os.path.exists(p):
            data = json.load(open(p, encoding='utf-8'))
            st = data.get('stations') or {}
            src = st if st else data.get('all', {})
            for k, v in src.items():
                c = clean_doc(v)
                if c:
                    docs[k] = c
    return docs


def load_station_identity(run: str) -> dict:
    """v9.17: load station_identity.json (root -> {display, code, doc, sort_key, ...}).
    Returns {} when absent — callers fall back to the v9.16 var_docs path."""
    if run:
        p = os.path.join(run, 'station_identity.json')
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding='utf-8'))
            except Exception:
                return {}
    return {}


def load_network_comments(run: str) -> dict:
    """v9.18: load network_comments.json (action_name -> [comment, ...]).
    Returns {} when absent."""
    if run:
        p = os.path.join(run, 'network_comments.json')
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding='utf-8'))
            except Exception:
                return {}
    return {}


def load_trace_runtime(run: str) -> dict:
    """v9.23: load trace_step_runtime.csv from trace_analysis/ subdirectory.
    Returns dict: (start_label, end_label) -> avg_duration_ms (int or None).
    Returns {} when absent (no trace analyzed — duration column stays empty)."""
    if run:
        p = os.path.join(run, 'trace_analysis', 'trace_step_runtime.csv')
        if os.path.exists(p):
            rt = {}
            try:
                import csv as _csv
                with open(p, encoding='utf-8-sig', newline='') as f:
                    for row in _csv.DictReader(f):
                        dur = (row.get('平均时长ms') or '').strip()
                        key = (row.get('开始标签', ''), row.get('结束标签', ''))
                        try:
                            rt[key] = int(dur) if dur else None
                        except ValueError:
                            rt[key] = None
                return rt
            except Exception:
                return {}
    return {}


_GENERIC_COMMENT = re.compile(
    r'^(启动条件|运行条件|互锁条件|报警|初始化|模式定义|工位计时|非同启同停|自动步计时|'
    r'网络注释|程序版本号|主控指示灯|步序描述|自动运行|模式跳转|记忆传递|数据传递|'
    r'IO转换|动子上电|动子自由|流程监控|命令传递|站点丢失|站点错误|'
    r'三色灯|指示灯|蜂鸣器|急停|安全门|光电开关)'
)


def _best_comments(comments: list, max_n: int = 2) -> str:
    """Pick the most specific comments (non-generic) from a list."""
    specific = [c for c in comments if not _GENERIC_COMMENT.match(c)]
    pool = specific if specific else comments
    return ' | '.join(pool[:max_n])


def _station_code(root: str) -> str:
    """Station number from the state-variable root.
    omron native structs 'stEM310' -> 'EM310' (strip leading st/St/ST only when
    followed by an uppercase letter); 'udt_EM01'/'udt_EM07_1' -> 'EM01'/'EM07_1'
    (strip udt_ prefix, v9.16); SC04-style roots ('ST06', 'HJXHX_ST06')
    are already the station number and kept verbatim.
    Returns '' for NaN/None/empty so it never leaks into display names."""
    s = str(root)
    if not s.strip() or s.lower() in ('nan', 'none'):
        return ''
    # v9.16: udt_EMxx -> EMxx (omron global user-data-type station structs)
    m = re.match(r'^udt_(EM\d+(?:_\d+)?)$', s)
    if m:
        return m.group(1)
    m = re.match(r'^[sS][tT]([A-Z].*)$', s)
    return m.group(1) if m else s


def workstation(comp: str, var_root: str = '', var_docs: dict = None,
                si_map: dict = None) -> str:
    """Resolve a workstation display name for a row.

    v9.20 priority:
      0. POU path in 构成要素 (EMxx_name::SCxx_...) — direct extraction,
         handles codesys array-type state machines where 构成要素 carries
         the full POU path.  This is the most specific source.
      1. station_identity.json (built by predict) — provides code, doc, sort_key.
         For array-type controllers (m_nNextAutoStep[N]), identity keys include
         the array index so each index resolves to a distinct workstation.
      2. 构成要素 SC04 Chinese suffix — sub-station granularity refinement.
      3. var_docs fallback (v9.16 path).
      4. bare code + section fallback (v9.16 path).
    """
    var_docs = var_docs or {}
    si_map = si_map or {}
    var_root = str(var_root) if pd.notna(var_root) and var_root else ''
    parts = [p.strip() for p in str(comp).split(';')]
    cjk = re.compile(r'[\u4e00-\u9fff]')

    # v9.20: POU path extraction (EMxx_name::SCxx_...)
    for p in parts:
        m = re.match(r'(EM\d+)_(.+?)(?:_L\d+.*)?::', p)
        if m and cjk.search(m.group(2)):
            code = m.group(1)
            name = re.sub(r'_L\d+.*$', '', m.group(2))
            return f'{code}_{name}'

    # v9.17/v9.20: station_identity — try indexed key first, then bare root.
    si_entry = si_map.get(var_root) if var_root else None
    if not si_entry and var_root:
        bare_root = re.sub(r'\[\d+\]', '', var_root)
        if bare_root != var_root:
            si_entry = si_map.get(bare_root)
    code = si_entry['code'] if si_entry else (_station_code(var_root) if var_root else '')

    # Sub-station granularity: SC04 Chinese suffix is finer than station_identity.
    # Use the station_identity CODE (never lose identity) + the sub-station name.
    best = None
    for p in parts:
        m = re.match(r'SC04_(?:RUN\d*_?|Run\d*_?)\s*(.+)', p)
        if m and m.group(1).strip():
            name = m.group(1).strip()
            if cjk.search(name):
                return f'{code}_{name}' if code else name
            best = best or name
        elif p.startswith('SC04') and cjk.search(p):
            return p

    # v9.17: station_identity display (station-level, e.g. EM04_长边清洗工位)
    if si_entry and si_entry.get('doc'):
        return si_entry['display']

    # v9.16 fallback: var_docs (station-level Chinese name)
    if var_root:
        root = var_root.split('.')[0].split('[')[0]
        if root in var_docs:
            zh = var_docs[root]
            return f'{code}_{zh}' if code else zh
    # non-Chinese best (e.g. '1' from 'SC04_Run1') as last resort before bare code
    if best:
        return f'{code}_{best}' if code else best
    # v9.16: bare section name (e.g. 'SC04_Run') with no Chinese suffix and no
    # var_docs hit — prefix the station code from the variable root so each EM
    # is a distinct workstation block instead of collapsing 12 EMs into one
    # mixed 'SC04_Run' block with broken step ordering.
    if code:
        sec = parts[0] if parts and parts[0].startswith('SC04') else ''
        return f'{code}_{sec}' if sec else code
    for p in parts:
        if p.startswith('SC04'):
            return p
    return parts[0] if parts else str(comp)


def _em_sort_key(name: str):
    """Numeric sort key for workstation names like 'EM07_1_长边焊接1工位'
    or 'ST06_铜上盘' — extracts (prefix_num, sub_num) so EM01<EM02<…<EM10
    and EM07_1<EM07_2; non-matching names sort last stably."""
    m = re.search(r'(?:EM|ST)(\d+)(?:_(\d+))?', str(name))
    if m:
        return (int(m.group(1)), int(m.group(2)) if m.group(2) else 0)
    return (10 ** 9, 0)


def cell_str(x) -> str:
    try:
        if pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x)


def pou_station(pou) -> str:
    first = cell_str(pou).split(";")[0].strip()
    area = first.split("::")[0].strip()
    return area or "未分组"


def pick_group_by(df) -> str:
    """auto: actual section naming -> 构成要素 (SC04 sections or equivalent omron
    struct paths like stEM310.ProcessCtrl.iCmdStep, which are equally per-workstation);
    fall back to 证据POU only when 构成要素 is absent/empty."""
    if "构成要素" in df.columns:
        comp = df["构成要素"].fillna("").astype(str)
        if comp.str.strip().ne("").mean() >= 0.5:
            return "构成要素"
    if "证据POU" in df.columns:
        return "证据POU"
    return "构成要素"


def var_of(label: str):
    m = re.match(r'\s*([A-Za-z_]\w*(?:\[[^\]]*\])?(?:\.\w+(?:\[[^\]]*\])?)*)\s*=', str(label))
    return m.group(1) if m else None


def load_action_map(run: str):
    """(state_variable, state_value) -> ordered unique action-variable list.
    PLCopen action trace and omron wired command evidence, source facts only."""
    amap = {}
    if run:
        p = os.path.join(run, 'plcopen_action_trace.csv')
        if os.path.exists(p):
            t = pd.read_csv(p, dtype=str)
            for _, r in t.iterrows():
                sym = r.get('target_symbol')
                if pd.isna(sym) or not str(sym).strip() or str(r.get('target_type')) != 'coil':
                    continue
                key = (str(r.get('state_variable')), str(r.get('state_value')))
                lst = amap.setdefault(key, [])
                if str(sym) not in lst:
                    lst.append(str(sym))
        p = os.path.join(run, 'native_command_evidence.csv')
        if os.path.exists(p):
            t = pd.read_csv(p, dtype=str)
            for _, r in t.iterrows():
                start = str(r.get('start', ''))
                if '=' not in start:
                    continue
                var, _, val = start.rpartition('=')
                arg = str(r.get('arguments', '')).strip()
                cmd = str(r.get('command', '')).strip()
                if not arg or cmd in ('coil_write',):
                    continue
                key = (var, val)
                lst = amap.setdefault(key, [])
                if arg not in lst:
                    lst.append(arg)
        # omron ST assignments: events carry the full guard containing the
        # state anchor; the assigned variable is that state's action write.
        p = os.path.join(run, 'indexed_events.csv')
        if os.path.exists(p):
            t = pd.read_csv(p, dtype=str)
            if 'command' in t.columns:
                st = t[t['command'].fillna('') == 'ST_assignment']
                anchor = re.compile(
                    r'([A-Za-z_]\w*(?:\[[^\]]*\])?(?:\.\w+(?:\[[^\]]*\])?)*)'
                    r'=([+-]?\d+)(?![\w#])')
                for _, r in st.iterrows():
                    arg = str(r.get('arguments', ''))
                    if ':=' not in arg:
                        continue
                    var_name = arg.split(':=', 1)[0].strip()
                    if not var_name:
                        continue
                    for var, val in set(anchor.findall(str(r.get('guard', '')))):
                        lst = amap.setdefault((var, val), [])
                        if var_name not in lst:
                            lst.append(var_name)
    return amap


def step_text(row, amap) -> str:
    s, e = str(row['开始标签']), str(row['结束标签'])
    vs, ve = var_of(s), var_of(e)
    acts = []
    if vs:
        m = re.search(r'=\s*(\S+)\s*$', s)
        if m:
            acts = amap.get((vs, m.group(1)), [])
    if acts:
        shown = acts[:4] + (['…'] if len(acts) > 4 else [])
        txt = '动作变量: ' + '; '.join(shown)
    elif vs and ve and vs == ve:
        ns = re.search(r'=\s*(\S+)\s*$', s)
        ne = re.search(r'=\s*(\S+)\s*$', e)
        txt = f'{vs}: {ns.group(1)}→{ne.group(1)}' if (ns and ne) else f'{s} → {e}'
    else:
        txt = f'{s} → {e}'
    notes = re.findall(r'\*[^*]{2,40}\*', str(row.get('动作描述', '') or ''))
    if notes:
        txt += '  ' + ' '.join(notes[:2])
    return txt


def generate(prediction, out=None, group_by='auto', proc_name=None, run=None, sheet='步序采集表'):
    """Build the workstation delivery xlsx. Inputs are read-only; returns stats dict.
    group_by='auto' picks 构成要素 for SC04 naming, else 证据POU when available.
    out/proc_name default like the CLI: <proc_name>_步序采集表.xlsx beside the prediction.
    Raises ValueError on missing columns (caller decides how to report)."""
    proc_name = proc_name or re.sub(r'_v\d+$', '', os.path.basename(os.path.normpath(os.path.dirname(prediction)))) or '工序'
    if not out:
        out = os.path.join(os.path.dirname(prediction), f'{proc_name}_步序采集表.xlsx')
    run = run or os.path.dirname(prediction)
    amap = load_action_map(run)
    # 动作描述（A档模板/B档LLM工艺语义，action_describe.py 产物；存在则升级 col C）
    # v9.19: fallback chain: action_desc_final.csv (B+A merged) -> action_desc_a.csv (A only)
    desc_map = {}
    desc_stats = None
    dp = os.path.join(run, 'action_desc_final.csv')
    if not os.path.exists(dp):
        dp = os.path.join(run, 'action_desc_a.csv')
    if os.path.exists(dp):
        dd = pd.read_csv(dp, dtype=str, encoding='utf-8-sig')
        # A档 has 'A档描述' not '动作描述'; normalize column names
        if 'A档描述' in dd.columns and '动作描述' not in dd.columns:
            dd = dd.rename(columns={'A档描述': '动作描述'})
        if '描述来源' not in dd.columns:
            dd['描述来源'] = 'A档(源码模板)'
        for _, r in dd.iterrows():
            vt = r.get('动作变量', '')
            vt = '' if pd.isna(vt) or str(vt).strip() in ('nan', 'None') else str(vt)
            desc_text = r.get('动作描述', '')
            desc_text = '' if pd.isna(desc_text) or str(desc_text).strip().lower() in ('nan', 'none') else str(desc_text).strip()
            desc_map[(str(r['开始标签']), str(r['结束标签']))] = (
                desc_text, vt, str(r.get('行性质', '')), str(r.get('描述来源', 'A档(源码模板)')))
        desc_stats = dd['描述来源'].value_counts().to_dict()
    df = pd.read_csv(prediction, dtype=str, encoding='utf-8-sig')
    for col in ('开始标签', '结束标签'):
        if col not in df.columns:
            raise ValueError(f'prediction file lacks column: {col}')
    if group_by == 'auto':
        group_by = pick_group_by(df)
    if group_by not in df.columns:
        raise ValueError(f'prediction file lacks column: {group_by} (needed by --group-by {group_by})')
    df['_变量'] = df['开始标签'].map(var_of)
    df['_序'] = df['开始标签'].map(
        lambda x: (lambda m: int(m.group(1)) if m else 10 ** 9)(re.search(r'=\s*([+-]?\d+)\s*$', str(x))))
    if group_by == '构成要素':
        var_docs = load_var_docs(run)
        si_map = load_station_identity(run)
        df['_变量根'] = df['_变量'].map(lambda v: (str(v).split('.')[0] if pd.notna(v) and v else ''))
        df['_工位'] = df.apply(lambda r: workstation(r['构成要素'], r['_变量根'], var_docs, si_map), axis=1)
    elif group_by == '证据POU':
        df['_工位'] = df['证据POU'].map(pou_station)
    else:
        df['_工位'] = df['控制器'].map(lambda x: cell_str(x).strip() or '未分组')
    # v9.17: sort by station_identity sort_key when available, else _em_sort_key;
    # then variable, then step number.  Numeric so EM01<EM02<…<EM10.
    si_map = load_station_identity(run)
    def _row_sort(row):
        vr = row.get('_变量根', '')
        if pd.notna(vr) and vr:
            if vr in si_map:
                return tuple(si_map[vr].get('sort_key', [10**9, 0]))
            # v9.20: try bare root (without array index) as fallback
            bare = re.sub(r'\[\d+\]', '', str(vr))
            if bare != str(vr) and bare in si_map:
                return tuple(si_map[bare].get('sort_key', [10**9, 0]))
        return _em_sort_key(row.get('_工位', ''))
    df['_工位序'] = df.apply(_row_sort, axis=1)
    df = df.sort_values(['_工位序', '_变量', '_序'], kind='mergesort').reset_index(drop=True)
    hits = sum(1 for _, row in df.iterrows() if step_text(row, amap).startswith('动作变量'))

    # v9.18: blind-spot flags for human review highlighting.
    #   A=动作描述不完整(有动作变量但描述是模板文本)  -> 浅黄
    #   B=跨工序步(开始标签变量根≠结束标签变量根)     -> 浅橙
    #   C=合并区间(合并ID非空，需人工确认合并合理性)   -> 浅蓝
    #   D=边界待核验(来源候选ID在final_atomic_review) -> 浅红
    # Priority D > B > C > A (most severe wins).
    _TEMPLATE_DESC = re.compile(r'相邻已观察|源码局部状态迁移|步进保持|0状态已出现|触发.*到达信号.*参与接收条件')
    review_cids = set()
    rp = os.path.join(run, 'final_atomic_review.csv') if run else ''
    if rp and os.path.exists(rp):
        try:
            rv = pd.read_csv(rp, dtype=str, encoding='utf-8-sig')
            review_cids = set(rv['候选ID'].dropna().unique())
        except Exception:
            pass
    # v9.20: single light-yellow for D (boundary review) and A (incomplete desc).
    # B (cross-station) and C (merged interval) are not errors — not colored.

    def _blind_flag(row):
        flags = []
        # D: boundary review
        if review_cids:
            for part in str(row.get('来源候选ID', '')).split(';'):
                if part.strip() and part.strip() in review_cids:
                    flags.append('D')
                    break
        # B: cross-station (start root != end root)
        # var_of requires '=value'; BOOL start labels (e.g. HJXHX_ST01.Signal[1])
        # have no '=' so fall back to the first identifier token.
        s_var = row['_变量']
        if pd.notna(s_var) and s_var:
            s_root = str(s_var).split('.')[0]
        else:
            m = re.match(r'\s*([A-Za-z_]\w*)', str(row['开始标签']))
            s_root = m.group(1) if m else ''
        e_label = str(row['结束标签'])
        e_var = var_of(e_label)
        if e_var:
            e_root = str(e_var).split('.')[0]
        else:
            m = re.match(r'\s*([A-Za-z_]\w*)', e_label)
            e_root = m.group(1) if m else ''
        if s_root and e_root and s_root != e_root:
            flags.append('B')
        # C: merged interval
        mid = str(row.get('合并ID', '') or '')
        if mid.strip() and mid.strip() != 'nan':
            flags.append('C')
        # A: action variable present but description is template
        stxt = step_text(row, amap)
        if stxt.startswith('动作变量') and _TEMPLATE_DESC.search(str(row.get('动作描述', '') or '')):
            flags.append('A')
        return flags

    df['_盲区'] = df.apply(_blind_flag, axis=1)
    blind_counts = {k: 0 for k in 'ABCD'}
    for fl in df['_盲区']:
        for f in fl:
            blind_counts[f] = blind_counts.get(f, 0) + 1

    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    thin = Side(style='thin', color='999999')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hfont = Font(name='Arial', bold=True, size=10)
    nfont = Font(name='Arial', size=8, color='666666')
    cfont = Font(name='Arial', size=9)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left = Alignment(horizontal='left', vertical='center', wrap_text=True)
    digest = hashlib.sha256()
    with open(prediction, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            digest.update(blk)
    sha16 = digest.hexdigest()[:16]
    # v9.23: load trace runtime map early so note + row fill can both use it.
    trace_rt = load_trace_runtime(run)
    note = (f'工序: {proc_name} | 来源: {os.path.basename(prediction)} | 行数: {len(df)} | 分组: {group_by} | '
            + (f'动作描述: B档(LLM工艺语义) {desc_stats.get("B档(LLM工艺语义)", 0)}条/A档(源码步注释) {desc_stats.get("A档(源码步注释)", 0)}条 | ' if desc_stats else '')
            + f'SHA256: {sha16}… | 排序仅为展示查找便利，不代表执行顺序或工艺路径；'
              '步序/标签/描述内容完全来自同一行预测记录与源码证据文件，无推断文本。'
              + (f' 时长(ms)与总时长由运行时trace跳变分析自动回填。' if trace_rt else ' 时长列为现场实测项，预测侧不填。')
              + (f' 备注列标记【待复核】=动作描述待完善(A={blind_counts["A"]})。'
                 if (blind_counts["A"]) else ''))
    ws.cell(row=1, column=2, value=f'{proc_name} 步序采集表（预测交付）').font = hfont
    nc = ws.cell(row=2, column=2, value=note)
    nc.font = nfont
    nc.alignment = left
    for rr in (1, 2):
        ws.merge_cells(start_row=rr, start_column=2, end_row=rr, end_column=8)
    # 精简列结构（v9.20）：构成要素|动作描述|时长|动作变量|开始标签|结束标签|备注
    heads = ('构成要素', '动作描述', '时长(ms)', '动作变量（引用）', '开始标签', '结束标签', '备注')
    for j, h in enumerate(heads, 2):
        c = ws.cell(row=3, column=j, value=h)
        c.font = hfont; c.alignment = center; c.border = border
        c.fill = PatternFill('solid', start_color='F2A08C' if j in (2, 6, 7, 8) else 'FFE9A8')

    # 预取每行动作描述（v9.21: desc_map优先，空描述时不再fallback到变量翻译）
    def _row_desc(row):
        key = (str(row['开始标签']), str(row['结束标签']))
        d = desc_map.get(key)
        if d:
            # d = (desc_text, var_text, is_b_tier)
            return d[0], d[1], d[3].startswith('B档')
        # No desc_map entry — use step_text only for the 动作变量 column (引用),
        # but leave the description itself empty (no variable names in desc column).
        stxt = step_text(row, amap)
        return '', stxt, False

    df['_dtxt'] = None
    df['_vtxt'] = None
    _bcount = 0
    for i, row in df.iterrows():
        dtxt, vtxt, is_b = _row_desc(row)
        if is_b:
            _bcount += 1
        df.at[i, '_dtxt'] = dtxt
        df.at[i, '_vtxt'] = vtxt

    # v9.18: load network comments for the 备注 column
    nc_map = load_network_comments(run)
    # v9.23: trace_rt already loaded above (before note construction).

    r = 4
    blocks = []  # (首行, 末行, 工位, 时长合计)
    # 统一字体和行高
    data_font = Font(name='Arial', size=9)
    total_font = Font(name='Arial', size=9, bold=True)
    data_align = Alignment(horizontal='left', vertical='center', wrap_text=False)
    center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    total_fill = PatternFill('solid', start_color='F0F0F0')
    for wsname, g in df.groupby('_工位', sort=False):
        b0 = r
        block_dur_sum = 0
        has_trace_dur = False
        for _, row in g.iterrows():
            ws.cell(row=r, column=2, value=wsname)
            # 动作描述（干净文本，不含标注）
            dtxt = str(row['_dtxt'])
            ws.cell(row=r, column=3, value=dtxt if dtxt and dtxt.lower() not in ('nan','none') else None)
            # v9.23: 时长(ms) — 从trace分析结果回填，无trace时留空
            rt_key = (str(row['开始标签']), str(row['结束标签']))
            rt_dur = trace_rt.get(rt_key) if trace_rt else None
            ws.cell(row=r, column=4, value=rt_dur if rt_dur is not None else None)
            if rt_dur is not None:
                block_dur_sum += rt_dur
                has_trace_dur = True
            # 动作变量（引用）
            vtxt = str(row['_vtxt']) if str(row['_vtxt']) not in ('nan', 'None') else ''
            ws.cell(row=r, column=5, value=vtxt if vtxt else None)
            ws.cell(row=r, column=6, value=row['开始标签'])
            ws.cell(row=r, column=7, value=row['结束标签'])
            # 备注：网络注释（取最具体的1条）+ 待复核标记
            remark_parts = []
            if nc_map:
                comp = str(row.get('构成要素', '') or '')
                best_nc = ''
                for part in comp.split(';'):
                    p = part.strip()
                    if p and p in nc_map:
                        comments = nc_map[p]
                        # 取最具体的注释（过滤泛化注释）
                        specific = [c for c in comments if not _GENERIC_COMMENT.match(c)]
                        if specific:
                            best_nc = specific[0]
                            break  # 只取第一条具体的
                if best_nc:
                    remark_parts.append(best_nc)
            # v9.22: 待复核标记写在备注列（仅标记动作描述不完整；D边界待核验覆盖率太高不标）
            needs_review = 'A' in row['_盲区'] and not str(row['_dtxt']).strip()
            if needs_review:
                remark_parts.append('【待复核】')
            if remark_parts:
                ws.cell(row=r, column=8, value=' | '.join(remark_parts))
            # 统一样式
            for j in range(2, 9):
                c = ws.cell(row=r, column=j)
                c.font = data_font
                c.border = border
                c.alignment = center_align if j == 2 else data_align
            # 待复核行的备注列用红色字体
            if needs_review:
                ws.cell(row=r, column=8).font = Font(name='Arial', size=9, color='FF0000', bold=True)
            # 统一行高
            ws.row_dimensions[r].height = 18
            r += 1
        # 块尾"总时长"行
        ws.cell(row=r, column=3, value='总时长')
        # v9.23: 有trace数据时填入该工位组时长合计
        if has_trace_dur:
            ws.cell(row=r, column=4, value=block_dur_sum)
        for j in range(2, 9):
            c = ws.cell(row=r, column=j)
            c.font = total_font
            c.border = border
            c.fill = total_fill
            c.alignment = data_align if j != 2 else center_align
        ws.row_dimensions[r].height = 18
        r += 1
        blocks.append((b0, r - 1, wsname, block_dur_sum if has_trace_dur else None))
    for b0, b1, _, _ in blocks:
        if b1 > b0:
            ws.merge_cells(start_row=b0, start_column=2, end_row=b1, end_column=2)
        ws.cell(row=b0, column=2).alignment = center_align
    # 列宽优化：动作描述最宽，标签次之，工位/时长/备注适中
    for col, w in (('B', 18), ('C', 42), ('D', 8), ('E', 28), ('F', 26), ('G', 26), ('H', 22)):
        ws.column_dimensions[col].width = w
    # 标题行行高
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 16
    ws.row_dimensions[3].height = 20
    ws.freeze_panes = 'A4'
    wb.save(out)
    return {'proc_name': proc_name, 'rows': len(df), 'action_variable_rows': hits,
            'workstation_groups': len(blocks), 'group_by': group_by,
            'b_desc_rows': _bcount if desc_stats else None, 'desc_sources': desc_stats,
            'blind_spot_counts': blind_counts,
            'source_sha16': sha16, 'amap_keys': len(amap), 'out': out,
            'trace_duration_backfilled': bool(trace_rt)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prediction', required=True)
    ap.add_argument('--out', default=None,
                    help='缺省: prediction 同目录/<工序名>_步序采集表.xlsx（最终交付文件命名）')
    ap.add_argument('--proc-name', default=None,
                    help='工序名（交付文件与标题命名）；缺省从 prediction 目录名推断并去掉 _vN 后缀')
    ap.add_argument('--sheet', default='步序采集表')
    ap.add_argument('--run', default=None, help='prediction run dir with action trace / command evidence')
    ap.add_argument('--group-by', choices=('auto', '构成要素', '证据POU', '控制器'), default='auto',
                    help='工位分组：auto=SC04命名用构成要素，否则证据POU；可显式指定')
    a = ap.parse_args()
    try:
        stats = generate(a.prediction, a.out, a.group_by, a.proc_name, a.run, a.sheet)
    except ValueError as exc:
        raise SystemExit(str(exc))
    print(f"proc-name: {stats['proc_name']}  rows: {stats['rows']}  action-variable rows: {stats['action_variable_rows']}  "
          f"workstation groups: {stats['workstation_groups']}  group-by: {stats['group_by']}  "
          f"source-sha16: {stats['source_sha16']}  ->  {stats['out']}")


if __name__ == '__main__':
    main()
