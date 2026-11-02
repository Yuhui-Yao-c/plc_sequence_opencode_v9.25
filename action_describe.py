# -*- coding: utf-8 -*-
"""动作描述生成器：A档=源码事实模板（机构词[文档]+动作词[成员词典]+引用），
B档=LLM工艺语义增强（批次机制+白名单校验+A档兜底）。
只读源码证据与预测产物；不读GT/人工表。每条描述可回溯引用。

用法:
  python action_describe.py a --run outputs/X           # A档 -> action_desc_a.csv
  python action_describe.py build --run outputs/X       # B档批次 -> batches_desc/*.input.json
  (OpenCode逐批写 batches_desc/*.result.json)
  python action_describe.py validate --run outputs/X    # 校验合并 -> action_desc_final.csv + action_desc_review.csv
"""
import argparse, json, os, re, sys, glob
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_workstation_xlsx import load_action_map, var_of, load_var_docs

WORDBOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'action_wordbook.json')
DASH_RE = re.compile(r'^[—…\-–\s]*$')


def load_wb():
    with open(WORDBOOK, encoding='utf-8') as f:
        return json.load(f)


def clean_doc(d):
    """变量文档 -> 纯中文机构词（GrpComment 提取、<Sub/> 截断、去标签）。"""
    if not d:
        return ''
    s = str(d)
    m = re.search(r'<GrpComment_zh-cn>(.*?)</GrpComment_zh-cn>', s, re.S)
    if m:
        s = m.group(1)
    s = re.split(r'<Sub\s*/?>', s)[0]
    s = re.sub(r'<[^>]+>', '', s)
    s = s.replace('\\r', '').replace('\\n', '').strip()
    return s


def classify_root_word(root, docs, wb):
    """根 -> (机构词, 来源)。docs 精确 -> roots 前缀兜底 -> 原文。"""
    for key in (root, re.sub(r'\[\d+\]', '', root)):
        d = clean_doc(docs.get(key, ''))
        if d:
            return d, 'doc'
    base = re.match(r'[A-Za-z_\u4e00-\u9fff]+', root)
    base = base.group(0) if base else root
    for pre, w in sorted(wb['roots'].items(), key=lambda kv: -len(kv[0])):
        if base.startswith(pre):
            return w, 'prefix'
    return root, 'raw'


def describe_token(tok, docs, wb):
    """token -> dict(word, action, kind, ref) 或 None(破折号/空)。

    v9.25: 在处理 rest（成员链）时，提取具体变量名（如 Cy_Up1、Axis_Z1）
    查 variable_documentation.json，将变量文档注入 word 字段，使 B 档
    agent 能看到"上料平移顶升气缸"而非仅"EM01_顶盖上料模组"。
    """
    s = str(tok).strip()
    if not s or DASH_RE.match(s):
        return None
    root_m = re.match(r'([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*(?:\[\d+\])?)', s)
    if not root_m:
        return {'word': s, 'action': '信号', 'kind': '信号', 'ref': s}
    root_full = root_m.group(1)
    rest = s[root_m.end():]
    rest = re.sub(r'^[\.\s]+', '', rest)
    root_idx = ''.join(re.findall(r'\[(\d+)\]', root_full))
    root_base = re.sub(r'\[\d+\]', '', root_full)
    word, src = classify_root_word(root_base, docs, wb)

    # v9.25: 从 rest 中提取具体变量名（第一个标识符），查变量文档。
    # 例如 EM01V.Cy_Up1.In.Auto_R → 提取 Cy_Up1 → 查 docs → "上料平移顶升气缸"
    # 用变量文档替换 word（比工位名更具体），使 B 档 agent 有更丰富的上下文。
    var_doc_word = ''
    if rest:
        first_var_m = re.match(r'([A-Za-z_]\w*)', rest)
        if first_var_m:
            var_name = first_var_m.group(1)
            # 查变量文档（尝试原名 + 去 [N] 后缀）
            for key in (var_name, re.sub(r'\[\d+\]', '', var_name)):
                d = clean_doc(docs.get(key, ''))
                if d:
                    var_doc_word = d
                    break
            if not var_doc_word:
                # 尝试 root_base.var_name 组合
                for key in (f'{root_base}.{var_name}', f'{root_base}.{var_name}'.split('.')[-1]):
                    d = clean_doc(docs.get(key, ''))
                    if d:
                        var_doc_word = d
                        break

    # 用变量文档替换 word（更具体），同时保留工位名作 fallback
    if var_doc_word:
        word = var_doc_word
        src = 'var_doc'
    if root_idx:
        word = f'{word}[{root_idx}]'
    chain = re.sub(r'\[\d+\]', '[N]', rest) if rest else ''
    last_seg = chain.split('.')[-1] if chain else ''
    sig = any(k and (k in chain or k == last_seg) for k in wb['signal_members'])
    action = None
    if chain:
        mb = wb['members'].get(chain)
        if not mb and '.' in chain:  # 两级成员链: 模块.成员
            mod, mem = chain.split('.', 1)
            modw = wb.get('modules', {}).get(mod)
            memw = wb['members'].get(mem) or wb['members'].get(re.sub(r'\[\d+\]', '[N]', mem))
            if modw and memw:
                action = f'{modw}·{memw}'
            elif memw:
                action = memw
            elif modw:
                action = modw
        if action is None:
            for pat, w in wb.get('member_patterns', {}).items():
                if re.fullmatch(pat, chain):
                    action = w
                    break
        if action is None:  # 前缀匹配仅限长键（≥6字符基串），防短前缀误吞语义
            for k, w in wb['members'].items():
                kb = k.upper().split('[')[0]
                if len(kb) >= 6 and chain.upper().startswith(kb):
                    action = w
                    break
        # v9.25: 如果 action 仍是英文原文（未被词典翻译），尝试用变量文档
        # 的最后一个成员链段翻译。例如 Cy_Up1.In.Auto_R 的 Auto_R → 查 members
        if action == chain or (action and re.search(r'[A-Za-z_]{3,}', action)):
            # 尝试提取最后一段查词典
            last_member = last_seg
            if last_member:
                for k, w in wb['members'].items():
                    kb = k.upper().split('[')[0]
                    if kb == last_member.upper():
                        action = w
                        break
        action = action or chain
    else:
        action = '置位信号'
        sig = True
    kind = '信号' if sig else '动作'
    return {'word': word, 'action': action, 'kind': kind, 'ref': s, 'src': src}


def describe_row(tokens, docs, wb):
    """行级A档描述 + 行性质。"""
    groups = []
    for t in tokens or []:
        d = describe_token(t, docs, wb)
        if not d:
            continue
        key = (d['word'], d['action'], d['kind'])
        g = next((x for x in groups if x[0] == key), None)
        if g:
            g[1].append(d['ref'])
        else:
            groups.append((key, [d['ref']]))
    desc = '; '.join(f'{k[0]}·{k[1]}' for k, _ in groups)
    kinds = {k[2] for k, _ in groups}
    if not groups:
        return '', '无动作变量'
    if kinds == {'信号'}:
        kind = '信号/标志'
    elif kinds == {'动作'}:
        kind = '机械动作'
    else:
        kind = '动作+信号'
    return desc, kind


def load_full_docs(run):
    """全量变量文档（all 全集 + clean 清理）。工位名逻辑(stations优先)不适用此处。"""
    p = os.path.join(run, 'variable_documentation.json')
    if not os.path.exists(p):
        return {}
    data = json.load(open(p, encoding='utf-8'))
    docs = {}
    for k, v in (data.get('all') or {}).items():
        c = clean_doc(v)
        if c:
            docs[k] = c
    return docs


def load_step_comments(run):
    """v9.21: Load step_comments.json (state_var=step_value -> Chinese comment).
    Returns {} when absent — callers fall back to CSV 动作描述 column."""
    p = os.path.join(run, 'step_comments.json')
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding='utf-8'))
        except Exception:
            return {}
    return {}


_STEP_PREFIX = re.compile(r'^步?\d+[，,\-：:]\s*')
_FLOW_MAP_RE = re.compile(r'\n\s*\d+.*?(?:流程|取|放|跳转)')
# Workflow-generated template texts (NOT source step comments) — filtered out
# so the variable-translation fallback handles these rows instead.
_TEMPLATE_RE = re.compile(
    r'相邻已观察|源码局部状态迁移|0状态已出现|触发.*到达信号.*参与接收条件'
    r'|步进保持|迁移（')


def clean_step_comment(raw: str):
    """Clean a source step comment for use as A-tier description.

    Strips leading '步N-' / 'N-' prefixes, takes the first meaningful line,
    and returns None for empty/template/flow-map-only comments.
    """
    s = str(raw or '').strip()
    if not s or s.lower() in ('nan', 'none'):
        return None
    # Filter workflow-generated template texts (not real source comments)
    if _TEMPLATE_RE.search(s):
        return None
    # Take first line (multi-line comments often contain flow maps)
    first_line = s.split('\n')[0].strip()
    # Strip leading step-number prefix: "步0-模式跳转" -> "模式跳转"
    first_line = _STEP_PREFIX.sub('', first_line).strip()
    if not first_line:
        return None
    # v9.21: clean English/variable artifacts from step comments
    # (same rules as extract_step_comments._clean_comment)
    first_line = first_line.replace('_', '')
    # Replace longer patterns first to avoid double-replacement
    for en, zh in [('Cu1', '铜1'), ('Cu', '铜'), ('AL1', '铝1'), ('AL', '铝'), ('Al', '铝'),
                   ('NG', '不良'), ('OK', '合格'), ('CCD', '视觉检测'), ('EIP', ''),
                   ('NC', ''), ('ID', '编号'),
                   ('A1相机', 'A1相机'),  # already has 相机, skip
                   ('A1', 'A1'),  # keep A1/B0/B1 as station identifiers (common in comments)
                   ('====', ''), ('圃', '')]:
        if en == 'A1相机':
            continue  # skip, no replacement needed
        first_line = first_line.replace(en, zh)
    # Remove remaining ASCII letter sequences ≥2 chars that are NOT station codes
    # (station codes = single letter + digits, e.g. A1, B0, B14 — keep these)
    # Variable names = 2+ letters or letters+digits, e.g. Cy114, NC11, AbsEx
    def _remove_var(m):
        s = m.group(0)
        # Keep single-letter station codes (A1, B14, etc.)
        if re.match(r'^[A-Z]\d+$', s):
            return s
        return ''
    first_line = re.sub(r'[A-Za-z]{1,}[A-Za-z0-9]*', _remove_var, first_line)
    first_line = re.sub(r'\s+', ' ', first_line).strip()
    if not first_line:
        return None
    return first_line


def cmd_a(run):
    wb = load_wb()
    docs = load_full_docs(run)
    amap = load_action_map(run)
    sc_map = load_step_comments(run)  # v9.21: step_comments.json
    # v9.24: read prediction_collection_pairs.csv (available after predict,
    # before finish) so A-tier can be generated in the predict phase.
    # Fallback to final_prediction_pairs.csv for older runs that only have it.
    src_csv = os.path.join(run, 'prediction_collection_pairs.csv')
    if not os.path.exists(src_csv):
        src_csv = os.path.join(run, 'final_prediction_pairs.csv')
    df = pd.read_csv(src_csv, dtype=str, encoding='utf-8-sig')
    rows = []
    n_step_json = 0   # descriptions from step_comments.json (source ST // comments)
    n_csv_comment = 0  # descriptions from CSV 动作描述 column (codesys step comments)
    n_empty = 0       # no step comment available — description left empty for B-tier
    for i, r in df.iterrows():
        s = str(r['开始标签'])
        e = str(r['结束标签'])
        vs = var_of(s)
        m = re.search(r'=\s*(\S+)\s*$', s)
        toks = amap.get((vs, m.group(1)), []) if (vs and m) else []
        # Translate action variables for kind classification (goes to 动作变量 column only)
        var_desc, var_kind = describe_row(toks, docs, wb)
        # v9.21: priority 1 — step_comments.json (extracted from source ST // comments)
        desc = ''
        if s in sc_map:
            desc = sc_map[s]
            kind = var_kind if toks else '步进'
            n_step_json += 1
        else:
            # v9.20: priority 2 — CSV 动作描述 column (codesys step comments like "20-请求取料")
            step_comment = clean_step_comment(r.get('动作描述', ''))
            if step_comment:
                desc = step_comment
                kind = var_kind if toks else '步进'
                n_csv_comment += 1
            else:
                # v9.21: no step comment available — leave description empty for B-tier LLM.
                # Variable translation is NOT put in the description column (it contains
                # English/variable names). It goes only to the 动作变量 column for reference.
                desc = ''
                kind = var_kind if toks else '步进'
                n_empty += 1
        rows.append({'行号': i, '开始标签': s, '结束标签': e,
                     '动作变量': '; '.join(toks), 'A档描述': desc, '行性质': kind})
    out = pd.DataFrame(rows)
    p = os.path.join(run, 'action_desc_a.csv')
    out.to_csv(p, index=False, encoding='utf-8-sig')
    stat = out['行性质'].value_counts().to_dict() if '行性质' in out.columns and len(out) > 0 else {}
    print(f'A档完成: {len(out)}行 -> {p}')
    print(f'行性质: {stat}')
    print(f'描述来源: 步注释JSON={n_step_json} CSV步注释={n_csv_comment} 待LLM补充={n_empty}')
    return p


def cmd_build(run, batch_size=40):
    p = os.path.join(run, 'action_desc_a.csv')
    df = pd.read_csv(p, dtype=str, encoding='utf-8-sig')
    # v9.21: only build B-tier batches for rows where A-tier description is empty
    # (i.e. no step comment was found).  Rows with step comments keep their A-tier desc.
    df = df[df['A档描述'].fillna('').str.strip() == ''].reset_index(drop=True)
    if len(df) == 0:
        print(f'B档批次: 0行需LLM补充（全部有步注释）')
        outdir = os.path.join(run, 'batches_desc')
        os.makedirs(outdir, exist_ok=True)
        return 0
    docs = load_full_docs(run)
    wb = load_wb()
    sc_map = load_step_comments(run)
    # 跨行上下文：全表相邻行（工位+A档描述+步注释）
    full = pd.read_csv(p, dtype=str, encoding='utf-8-sig')
    ctx = []
    for _, r in full.iterrows():
        ws = classify_root_word(str(r['开始标签']).split('.')[0], docs, wb)[0]
        a_desc = str(r.get('A档描述', '') or '').strip()
        # v9.25: 加入步注释作为上下文（start 和 end 标签的注释）
        start_sc = sc_map.get(str(r['开始标签']), '')
        end_sc = sc_map.get(str(r['结束标签']), '')
        ctx.append({'ws': ws, 'a': a_desc, 'sc': start_sc or end_sc})
    outdir = os.path.join(run, 'batches_desc')
    os.makedirs(outdir, exist_ok=True)
    # v9.22: do NOT delete existing result.json — finish re-runs cmd_build()
    # each time, and agent's results must survive until cmd_validate() merges them.
    # Only clear stale input.json (they are regenerated every time).
    for f in glob.glob(os.path.join(outdir, '*.input.json')):
        os.remove(f)
    nb = 0
    for bi in range(0, len(df), batch_size):
        chunk = df.iloc[bi:bi + batch_size]
        rows = []
        for j, (_, r) in enumerate(chunk.iterrows()):
            # 在全表中找到当前行的位置以获取上下文
            rid = str(r['行号'])
            full_idx = full.index[full['行号'] == rid].tolist()
            gi = full_idx[0] if full_idx else 0
            toks = [t.strip() for t in str(r['动作变量']).split(';') if t.strip()]
            tdocs = []
            for t in toks:
                d = describe_token(t, docs, wb)
                tdocs.append({'tok': t, 'word': d['word'] if d else '', 'action': d['action'] if d else ''})
            # v9.25: 加入当前行的步注释（start/end 标签对应的源码注释）
            cur_start_sc = sc_map.get(str(r['开始标签']), '')
            cur_end_sc = sc_map.get(str(r['结束标签']), '')
            rows.append({'id': rid, 'start': r['开始标签'], 'end': r['结束标签'],
                         'a_desc': r['A档描述'], 'kind': r['行性质'], 'tokens': tdocs,
                         'step_comment': cur_start_sc or cur_end_sc,
                         'prev': ctx[gi - 1] if gi > 0 else None,
                         'next': ctx[gi + 1] if gi + 1 < len(ctx) else None})
        nb += 1
        payload = {'batch_id': nb, 'rows': rows,
                    'rules': {'max_len': 25,
                             'refs_must_be_tokens': True,
                             'verbs_whitelist': wb['verbs_whitelist'],
                             'no_invented_tokens': True,
                             'allow_generic_mechanism_words': True,
                             'desc_must_be_chinese_only': True},
                    'task': '为每行生成一句简短的纯中文工艺动作描述（如"Z轴下降到取料位""夹紧气缸张开""支架上料Z轴伺服定位"）。'
                            '一行只描述一个主要动作重点，不要把所有tokens拼接到一起。'
                            '多token时选择最核心的机械动作（伺服定位>气缸动作>信号），忽略次要信号和报警。'
                            '有step_comment时优先使用步注释。参考prev/next的sc获取上下文。'
                            '描述不含变量名/英文/ASCII标识符，长度≤20字符。'
                            '允许使用Z轴/X轴/Y轴/R轴/气缸/电夹爪/吸盘/机器人等通用机构词。'
                            'refs字段填写该行所有token的原始变量名。'}
        with open(os.path.join(outdir, f'batch_{nb:03d}.input.json'), 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f'B档批次: {nb}批 / {len(df)}行（待LLM补充） -> {outdir}')
    return nb


def cmd_validate(run):
    """读 batches_desc/*.result.json -> 白名单校验 -> action_desc_final.csv(全行) + review。"""
    wb = load_wb()
    verbs = set(wb['verbs_whitelist'])
    word_allow = set()
    for w in wb['members'].values():
        word_allow.add(w)
    for w in wb.get('modules', {}).values():
        word_allow.add(w)
    for w in wb['roots'].values():
        word_allow.add(w)
    # v9.21: generic mechanism words allowed in B-tier descriptions (not required
    # to be in tokens — these are universal engineering terms like Z轴/X轴/气缸).
    _GENERIC_MECH = {
        'Z轴', 'X轴', 'Y轴', 'R轴', '气缸', '电夹爪', '电爪', '吸盘', '机器人',
        '移载机构', '升降机构', '夹爪', '真空', '夹具', '机械手', '伺服',
        '取料位', '放料位', '安全位', '焊接位', '原点', '取盘位', '放盘位',
        '待料位', '检测位', '上料位', '下料位', '放料', '取料', '到', '回',
        '开始', '结束', '完成', '到位', '信号', '等待', '请求', '允许',
        '下降', '上升', '平移', '旋转', '前进', '后退', '伸出', '缩回',
        '夹紧', '张开', '松开', '吸附', '破真空', '回零', '复位', '移栽',
        '扫码', '检测', '焊接', '点胶', '贴胶', '拍照', '数据', '传递',
        '进站', '出站', '触发', '确认', '计时', '屏蔽', '判断', '预留',
        '模式', '跳转', '流程', '循环', '工位', '载具', '物料', '电芯',
        '顶盖', '转接片', '底托', '蓝胶', '吸塑盘', '铝壳', '入壳',
        '合芯', '下料', '上料', '治具', '回流', '备料', '热熔',
        '步进过渡', '信号触发', '待人工补充',
    }
    allowed = verbs | word_allow | _GENERIC_MECH
    a = pd.read_csv(os.path.join(run, 'action_desc_a.csv'), dtype=str, encoding='utf-8-sig')
    docs = load_full_docs(run)
    ascii_id = re.compile(r'[A-Za-z_][A-Za-z0-9_\[\]\.]*')
    # 每行重算机构词/动作词集合（与A档同源），作为子代理可引用词汇
    row_lex = {}
    for _, r in a.iterrows():
        toks = [t.strip() for t in str(r['动作变量']).split(';') if t.strip()]
        words, actions = [], []
        for t in toks:
            d = describe_token(t, docs, wb)
            if d:
                words.append(d['word'])
                actions.append(d['action'])
        row_lex[str(r['行号'])] = (toks, words, actions)
    bmap = {}
    issues = []
    for fp in sorted(glob.glob(os.path.join(run, 'batches_desc', '*.result.json'))):
        with open(fp, encoding='utf-8') as f:
            res = json.load(f)
        for rid, d in res.get('descs', {}).items():
            if not d.get('ok'):
                issues.append((rid, f"B档标记不可用: {d.get('reason', '')}"))
                continue
            desc = str(d.get('desc', '')).strip()
            refs = [str(x).strip() for x in d.get('refs', [])]
            if not desc:
                issues.append((rid, '空描述'))
                continue
            if len(desc) > 30:
                issues.append((rid, f'超长{len(desc)}'))
                continue
            if rid not in row_lex:
                issues.append((rid, '行号不存在'))
                continue
            toks, words, actions = row_lex[rid]
            bad = [x for x in refs if x not in toks]
            if bad:
                issues.append((rid, f'引用超出该行动作变量: {bad[:2]}'))
                continue
            # v9.21: desc must be Chinese-only — reject ANY ASCII identifier (≥2 chars)
            # This is stricter than before: no variable names in description column.
            en_in_desc = {x for x in ascii_id.findall(desc) if len(x) >= 2}
            if en_in_desc:
                issues.append((rid, f'描述含英文/变量名: {sorted(en_in_desc)[:3]}'))
                continue
            # 中文词白名单：desc 中每个连续中文串(≥2字)须命中 白名单词(含通用机构词)
            # 或该行机构词/A档描述词；否则视为编造工艺词。
            zh_segs = re.findall(r'[\u4e00-\u9fff]{2,}', desc)
            row_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', ' '.join(words)))
            row_words |= set(re.findall(r'[\u4e00-\u9fff]{2,}', ' '.join(actions)))
            row_words |= set(re.findall(r'[\u4e00-\u9fff]{2,}', str(a[a['行号'] == rid]['A档描述'].iloc[0])))
            bad_zh = [z for z in zh_segs
                      if not any(z in w or w in z for w in (allowed | row_words))]
            if bad_zh:
                issues.append((rid, f'编造中文词: {bad_zh[:3]}'))
                continue
            bmap[str(rid)] = {'desc': desc, 'refs': refs, 'kind': d.get('kind') or a[a['行号'] == rid]['行性质'].iloc[0]}
    rows = []
    n_b = 0
    n_a = 0
    n_empty = 0
    for _, r in a.iterrows():
        rid = str(r['行号'])
        if rid in bmap:
            d = bmap[rid]
            desc, kind, src = d['desc'], d['kind'], 'B档(LLM工艺语义)'
            n_b += 1
        elif str(r['A档描述']).strip():
            desc, kind, src = r['A档描述'], r['行性质'], 'A档(源码步注释)'
            n_a += 1
        else:
            desc, kind, src = '', r['行性质'], '待补充'
            n_empty += 1
        rows.append({'行号': rid, '开始标签': r['开始标签'], '结束标签': r['结束标签'],
                     '动作变量': r['动作变量'], '动作描述': desc, '行性质': kind, '描述来源': src})
    final = pd.DataFrame(rows)
    fp = os.path.join(run, 'action_desc_final.csv')
    final.to_csv(fp, index=False, encoding='utf-8-sig')
    rv = pd.DataFrame([{'行号': rid, '原因': why,
                        '兜底A档': a[a['行号'] == str(rid)]['A档描述'].iloc[0] if not a[a['行号'] == str(rid)].empty else ''}
                       for rid, why in issues])
    rp = os.path.join(run, 'action_desc_review.csv')
    rv.to_csv(rp, index=False, encoding='utf-8-sig')
    print(f'最终动作描述: {len(final)}行 (B档 {n_b} / A档步注释 {n_a} / 待补充 {n_empty}) -> {fp}')
    if issues:
        print(f'待核验: {len(issues)}条 -> {rp}')
    else:
        print('待核验: 0条')
    return fp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=('a', 'build', 'validate'))
    ap.add_argument('--run', required=True)
    ap.add_argument('--batch-size', type=int, default=40)
    x = ap.parse_args()
    if x.cmd == 'a':
        cmd_a(x.run)
    elif x.cmd == 'build':
        cmd_build(x.run, x.batch_size)
    else:
        cmd_validate(x.run)


if __name__ == '__main__':
    main()
