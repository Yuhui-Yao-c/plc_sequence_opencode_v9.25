# -*- coding: utf-8 -*-
"""Extract step-level comments from PLC source XML (source facts only).

Two generic extraction rules based on XML format (not project-specific):

1. PLCopen format (超声/转接片/顶盖焊):
   ST code in <STCode> elements.  Step comments are line-trailing //annotations
   on assignment lines that contain a state-variable=step-value comparison:
     abEMxxActionStart[N] := STxx.AutoStep[x]=value; //中文描述
     gaxActionTrig[N] := (udt_EMxx.adiAutoStep[x]=value); //中文描述
   Generic regex: := ... var[idx]=value ...; //中文

2. omron IEC61131-10 with CASE state machines (配对机):
   ST code in <BodyContent xsi:type="smcext:StSection"> → <ST>.
   Step comments are CASE branch labels with trailing // or strStepInfo:
     CASE stEMxxx.ProcessCtrl.iCmdStep OF
       0://等待启动信号
         stEMxxx.ProcessCtrl.strStepInfo:='等待启动信号';
       10://记忆校验
         ...

3. omron FBD format (包mylar): no text-extractable step comments.
   Falls through silently — v9.20 already handles these via CSV 动作描述 column.

Output: step_comments.json
  {"ST10.AutoStep[1]=50": "Cu焊接1_焊接开始", ...}
  Key format matches prediction start labels (状态变量=步值).

Usage:
  python extract_step_comments.py --source "source.xml" --out "outputs/run/step_comments.json"
"""
import argparse, json, re, sys
from lxml import etree


def local(e):
    return etree.QName(e).localname if isinstance(e.tag, str) else ''


# --- PLCopen: assignment line with state-comparison + trailing //comment ---
# Matches:  :=  ...  var[expr]=value  ...;  //中文
# var is a dotted path with array index (e.g. ST10.AutoStep[1], udt_EM01.adiAutoStep[0])
_PLCOPEN_STEP_RE = re.compile(
    r':='                       # assignment operator
    r'.*?'                      # prefix (parentheses, conditions before the state compare)
    r'([\w.]+\[\d+\])'         # state variable with array index
    r'\s*=\s*'                 # comparison equals
    r'(\d+)'                   # step value
    r'[^;\n]*;'                # rest until semicolon
    r'\s*//\s*'                # comment marker
    r'([^\n]+?)\s*$',          # comment text (trimmed)
    re.MULTILINE
)

# --- omron CASE: branch label + //comment ---
# Matches:  100://中文描述  (indented inside CASE block)
_CASE_LABEL_RE = re.compile(
    r'^\s*(\d+)\s*:\s*//'    # step number label + colon + //
    r'\s*(.+?)\s*$',         # comment text
    re.MULTILINE
)

# --- omron CASE: strStepInfo string assignment (backup) ---
_STEPINFO_RE = re.compile(
    r"strStepInfo\s*:=\s*'([^']+)'"
)

# --- CASE header: CASE <variable> OF ---
_CASE_HEADER_RE = re.compile(
    r'CASE\s+([\w.]+)\s+OF',
    re.IGNORECASE
)

# --- Filter: keep only lines with meaningful Chinese content ---
_MIN_CJK = re.compile(r'[\u4e00-\u9fff]')

# Comments that are not real action descriptions (timing/completion templates)
_NOISE_COMMENT = re.compile(r'^(计时开始|计时结束|计时)$')


def _is_meaningful(text: str) -> bool:
    """Step comment must contain at least one CJK character."""
    t = text.strip().rstrip(';').strip()
    if not t or len(t) < 2:
        return False
    return bool(_MIN_CJK.search(t))


def _is_noise(text: str) -> bool:
    """Filter out timing/completion template comments."""
    return bool(_NOISE_COMMENT.match(text.strip()))


def _clean_comment(text: str) -> str:
    """Clean a step comment: strip whitespace, trailing semicolons.
    If the comment accidentally includes code before //, keep only the part after //.
    Also normalize _Z轴/_X轴 to Z轴/X轴 (underscore before CJK is a separator, not English)."""
    t = text.strip()
    # Safety: if the extracted text contains := or // it means the regex
    # captured the full line instead of just the comment.  Keep only the
    # text after the last //.
    if '//' in t:
        t = t.rsplit('//', 1)[-1].strip()
    t = t.rstrip(';').strip()
    # Normalize: remove underscores (step comments use _ as separator, e.g.
    # "机台上料_Z轴下降" -> "机台上料Z轴下降").  Underscores in step comments
    # are never part of variable names — they separate Chinese segments.
    t = t.replace('_', '')
    # Replace common element/quality abbreviations with Chinese
    # (use simple replace — \b word boundaries don't work between CJK and ASCII)
    for en, zh in [('NG', '不良'), ('OK', '合格'), ('Cu1', '铜1'), ('Cu', '铜'),
                   ('AL1', '铝1'), ('Al', '铝'), ('AL', '铝')]:
        t = t.replace(en, zh)
    t = re.sub(r'\s+', ' ', t)
    return t


def _pick_best(candidates: list) -> str:
    """From a list of (comment, is_noise) tuples, pick the best one.
    Priority: non-noise > noise; first occurrence within each tier."""
    meaningful = [c for c, n in candidates if not n]
    if meaningful:
        return meaningful[0]
    if candidates:
        return candidates[0][0]
    return ''


def _detect_format(source: str) -> str:
    """Detect XML format from root element namespace.
    Returns 'plcopen' or 'omron'."""
    for _, e in etree.iterparse(source, events=('start',), huge_tree=True,
                                resolve_entities=False, no_network=True):
        tag = local(e)
        ns = e.nsmap.get(None, '') if e.nsmap else ''
        e.clear()
        if 'plcopen.org' in ns or tag == 'project':
            return 'plcopen'
        if 'ia.omron.com' in ns or 'iec.ch' in ns or tag == 'Project':
            return 'omron'
        break
    return 'plcopen'  # default


def _extract_plcopen(source: str) -> dict:
    """Extract step comments from PLCopen XML <STCode> elements.
    Returns {state_var=step_value: comment}.

    Multiple lines may map to the same (var, value) key — e.g. ActionStart
    has the action description, ActionDone has "完成", and timing lines have
    "计时开始/结束".  We collect all candidates and prefer non-noise comments.
    """
    # key -> list of (comment, is_noise)
    candidates = {}
    for _, e in etree.iterparse(source, events=('end',), tag='{*}STCode',
                                huge_tree=True, resolve_entities=False, no_network=True):
        text = e.text or ''
        if not text:
            e.clear()
            continue
        for m in _PLCOPEN_STEP_RE.finditer(text):
            state_var, step_val, comment = m.group(1), m.group(2), m.group(3)
            comment = _clean_comment(comment)
            if not _is_meaningful(comment):
                continue
            key = f'{state_var}={step_val}'
            candidates.setdefault(key, []).append((comment, _is_noise(comment)))
        e.clear()
        while e.getprevious() is not None:
            p = e.getparent()
            if p is not None:
                del p[p.index(e) - 1]
            else:
                break
    return {k: _pick_best(v) for k, v in candidates.items()}


def _extract_omron_case(source: str) -> dict:
    """Extract step comments from omron XML <BodyContent xsi:type="smcext:StSection">.
    Returns {state_var=step_value: comment}."""
    result = {}
    XSI = 'http://www.w3.org/2001/XMLSchema-instance'
    for _, e in etree.iterparse(source, events=('end',), tag='{*}BodyContent',
                                huge_tree=True, resolve_entities=False, no_network=True):
        xtype = e.get(f'{{{XSI}}}type', '')
        if 'StSection' not in xtype:
            e.clear()
            continue
        # Get ST text from this BodyContent
        st_text = ''
        for st in e.iter('{*}ST'):
            st_text = st.text or ''
            break
        if not st_text:
            e.clear()
            continue
        # Find CASE blocks and extract step labels
        # Strategy: scan line by line, track current CASE variable
        case_var = None
        for line in st_text.split('\n'):
            # Check for CASE header
            cm = _CASE_HEADER_RE.search(line)
            if cm:
                case_var = cm.group(1)
                continue
            # Check for END_CASE
            if re.search(r'\bEND_CASE\b', line, re.IGNORECASE):
                case_var = None
                continue
            # Inside CASE block: look for step label comments
            if case_var:
                lm = _CASE_LABEL_RE.match(line)
                if lm:
                    step_val, comment = lm.group(1), lm.group(2)
                    comment = _clean_comment(comment)
                    if _is_meaningful(comment) and case_var:
                        key = f'{case_var}={step_val}'
                        if key not in result:
                            result[key] = comment
        e.clear()
        while e.getprevious() is not None:
            p = e.getparent()
            if p is not None:
                del p[p.index(e) - 1]
            else:
                break
    return result


def extract(source: str, out: str) -> dict:
    """Extract step-level comments from PLC source XML.
    Writes step_comments.json and returns the dict.
    Safe to import from plc_cli.predict."""
    fmt = _detect_format(source)
    if fmt == 'plcopen':
        result = _extract_plcopen(source)
    else:
        # Try CASE extraction; omron FBD (包mylar) yields empty — that's OK
        result = _extract_omron_case(source)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def main():
    ap = argparse.ArgumentParser(description='Extract step-level comments from PLC source XML')
    ap.add_argument('--source', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    result = extract(a.source, a.out)
    # Stats
    cjk_count = sum(1 for v in result.values() if _MIN_CJK.search(v))
    print(f'step_comments: {len(result)} entries ({cjk_count} with CJK) -> {a.out}')
    if result:
        print('样例:')
        for k, v in list(result.items())[:5]:
            print(f'  {k} -> {v}')


if __name__ == '__main__':
    main()
