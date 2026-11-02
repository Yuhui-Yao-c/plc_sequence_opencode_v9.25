# -*- coding: utf-8 -*-
"""Station identity resolver — builds station_identity.json from predict products.

This module establishes a *station identity map* during the predict stage so that
the presentation layer (gen_workstation_xlsx.py) never has to guess workstation
names from naming conventions.  It replaces the prior pattern of adding one
hardcoded regex per project (udt_EMxx, STxx, …) with a single source of truth.

Inputs (all read-only, all produced by predict before batches run):
  - variable_documentation.json  : {all: {name: doc}, stations: {name: doc}}
    produced by extract_variable_docs.extract() (v9.16, auto-called by predict)
  - prediction_candidate_pairs.csv : the “控制器” column holds state variables
    like “udt_EM01.adiAutoStep[5]” / “ST06.AutoStep[3]”.  The variable ROOT is
    the part before the first “.”  Only roots that actually appear as state
    controllers are given an identity — auxiliary struct docs (HJXHX_ST08 etc.)
    that never anchor a state machine are NOT identities.

Output: station_identity.json
  {
    "udt_EM04": {
      "display": "EM04_长边清洗工位",
      "code": "EM04",
      "doc": "长边清洗工位",
      "sort_key": [4, 0],
      "source": "variable_documentation",
      "needs_review": false
    }, ...
  }

Identity key = the *full* variable root (no namespace stripping).
  HJXHX_ST08 (铝焊2) and ST08 (滚筒线) are DIFFERENT stations; stripping the
  namespace prefix would collapse them — never do that.

Code extraction (for display + sort):
  Only IEC61131-3 standard type prefixes (udt_, st, St, ST when followed by an
  uppercase letter) are stripped for the *display code*.  Namespace prefixes
  (HJXHX_, TJXHX_, AlWeld_, …) are preserved because they distinguish identities.

Review policy:
  A root with no documentation gets needs_review=true and display=<root itself>.
  predict writes a station_identity audit segment into candidate_audit.json so
  undocumented roots are visible *before* the delivery workbook is generated.

Usage (CLI):
  python station_identity.py --run outputs/<run>
  python station_identity.py --run outputs/<run> --candidates prediction_candidate_pairs.csv
"""
import argparse, json, os, re, csv


# IEC61131-3 standard type prefixes that can be safely stripped for DISPLAY only.
# udt_ = user-defined type; st/St/ST followed by uppercase = struct type prefix.
# These never carry namespace identity — HJXHX_/TJXHX_/AlWeld_ etc. do, so they
# are NOT in this set and are always preserved.
_TYPE_PREFIX = re.compile(r'^(?:udt_|[sS][tT])(?=[A-Z])')


def _clean_doc(text: str) -> str:
    """Documentation may wrap a GrpComment XML fragment; keep the plain zh name."""
    t = str(text)
    m = re.search(r'<GrpComment_zh-cn>(.*?)</GrpComment_zh-cn>', t, re.S)
    if m:
        t = m.group(1)
    i = t.find('<Sub/>')
    if i >= 0:
        t = t[:i]
    t = re.sub(r'<[^>]+>', '', t)
    return re.sub(r'\s+', '', t).strip()


def _display_code(var_root: str) -> str:
    """Extract the display code by stripping ONLY standard type prefixes.

    udt_EM04 -> EM04; stEM310 -> EM310; HJXHX_ST08 -> HJXHX_ST08 (preserved);
    ST06 -> ST06 (no prefix to strip); '' -> ''.
    """
    s = str(var_root)
    if not s.strip() or s.lower() in ('nan', 'none'):
        return ''
    return _TYPE_PREFIX.sub('', s)


def _sort_key(var_root: str):
    """Numeric sort key extracted from the LAST number group in the root.

    EM01 -> (1,0); EM07_1 -> (7,1); HJXHX_ST08 -> (8,0); ST06 -> (6,0).
    Non-numeric roots sort last stably.  Uses the last group so HJXHX_ST08
    (8) < HJXHX_ST10 (10), not (0,) < (0,) by the HJXHX prefix.
    """
    s = str(var_root)
    groups = re.findall(r'(\d+)(?:_(\d+))?', s)
    if not groups:
        return (10 ** 9, 0)
    last = groups[-1]
    return (int(last[0]), int(last[1]) if last[1] else 0)


def _extract_roots_from_candidates(candidates_csv: str):
    """Read the 控制器 column from prediction_candidate_pairs.csv and return
    the set of variable roots (part before first '.').

    This is the authoritative list of state-machine-anchoring variables —
    auxiliary struct docs that never appear as a controller are not identities.
    """
    roots = set()
    if not candidates_csv or not os.path.exists(candidates_csv):
        return roots
    with open(candidates_csv, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            ctrl = (row.get('控制器') or '').strip()
            if ctrl:
                roots.add(ctrl.split('.')[0].split('[')[0])
    return roots


def _extract_controller_pou_map(candidates_csv: str):
    """Read 控制器 and 证据POU columns, return {controller_with_index: {pou_area: count}}.

    For array-type controllers like m_nNextAutoStep[2], this maps each indexed
    controller to its POU evidence so we can derive workstation names.
    """
    cmap = {}
    if not candidates_csv or not os.path.exists(candidates_csv):
        return cmap
    with open(candidates_csv, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            ctrl = (row.get('控制器') or '').strip()
            pou = (row.get('证据POU') or '').strip()
            if not ctrl or not pou or '[' not in ctrl:
                continue
            # Take first POU's area (before ::)
            first_pou = pou.split(';')[0].strip()
            area = first_pou.split('::')[0].strip()
            if not area:
                continue
            cmap.setdefault(ctrl, {})
            cmap[ctrl][area] = cmap[ctrl].get(area, 0) + 1
    return cmap


def _pou_area_to_station(area: str):
    """Extract (code, name) from a POU area like 'EM02_电芯上料_滚轮线_L02_L04_L80_L86'.

    Returns (code, name) or (None, None) if not an EM-type POU area.
    code = 'EM02', name = '电芯上料_滚轮线' (strips _Lxx suffixes).
    """
    m = re.match(r'(EM\d+)_(.+?)(?:_L\d+.*)?$', area)
    if m:
        code = m.group(1)
        name = m.group(2)
        name = re.sub(r'_L\d+.*$', '', name)
        return code, name
    return None, None


def build_identity(run_dir: str, candidates_csv: str = None) -> dict:
    """Build the station identity map for a run directory.

    Returns the identity dict (root -> {display, code, doc, sort_key, source,
    needs_review}).  Also returns audit stats via the 'audit' sub-dict when
    called internally; callers use the top-level dict.

    Parameters
    ----------
    run_dir : str
        Path to the predict run directory.
    candidates_csv : str, optional
        Override path to the candidate pairs CSV.  Defaults to
        <run_dir>/prediction_candidate_pairs.csv.
    """
    run_dir = os.path.normpath(run_dir)
    candidates_csv = candidates_csv or os.path.join(run_dir, 'prediction_candidate_pairs.csv')
    vdocs_path = os.path.join(run_dir, 'variable_documentation.json')

    # 1. authoritative variable roots from the candidate pool
    roots = _extract_roots_from_candidates(candidates_csv)

    # 2. variable documentation (may be absent if extract_variable_docs failed)
    vdocs = {}
    if os.path.exists(vdocs_path):
        data = json.load(open(vdocs_path, encoding='utf-8'))
        stations = data.get('stations') or {}
        all_docs = data.get('all') or {}
        # stations is the curated subset (type name contains 'station');
        # fall back to all_docs for roots not in stations
        for k, v in stations.items():
            vdocs[k] = v
        for k, v in all_docs.items():
            vdocs.setdefault(k, v)

    identity = {}
    undocumented = []
    for root in sorted(roots):
        if not root or root.lower() in ('nan', 'none'):
            continue
        code = _display_code(root)
        sk = _sort_key(root)
        raw_doc = vdocs.get(root, '')
        doc = _clean_doc(raw_doc) if raw_doc else ''
        if doc:
            display = f'{code}_{doc}' if code else doc
            identity[root] = {
                'display': display,
                'code': code,
                'doc': doc,
                'sort_key': list(sk),
                'source': 'variable_documentation',
                'needs_review': False,
            }
        else:
            identity[root] = {
                'display': code or root,
                'code': code,
                'doc': '',
                'sort_key': list(sk),
                'source': 'none',
                'needs_review': True,
            }
            undocumented.append(root)

    # v9.20: array-type state machines (e.g. m_nNextAutoStep[N]).
    # When a single root appears with multiple array indices, each index is a
    # distinct workstation.  Derive the station name from the 证据POU column —
    # the most common EM-area for that controller index wins.
    ctrl_pou_map = _extract_controller_pou_map(candidates_csv)
    array_entries = 0
    for ctrl, area_counts in ctrl_pou_map.items():
        # ctrl is like 'm_nNextAutoStep[2]'; extract root and index
        m = re.match(r'^(.+?)\[(\d+)\]$', ctrl)
        if not m:
            continue
        root, idx_str = m.group(1), m.group(2)
        idx = int(idx_str)
        # Only treat as array-type if the root has multiple indices
        sibling_indices = [int(re.search(r'\[(\d+)\]$', k).group(1))
                           for k in ctrl_pou_map if k.startswith(root + '[')]
        if len(sibling_indices) < 2:
            continue
        # Most common POU area for this controller index
        best_area = max(area_counts, key=area_counts.get)
        em_code, em_name = _pou_area_to_station(best_area)
        if not em_code:
            continue
        identity_key = f'{root}[{idx}]'
        identity[identity_key] = {
            'display': f'{em_code}_{em_name}',
            'code': em_code,
            'doc': em_name,
            'sort_key': [int(re.search(r'(\d+)', em_code).group(1)), 0],
            'source': 'pou_evidence',
            'needs_review': False,
        }
        array_entries += 1

    return identity, {
        'total_var_roots': len(roots),
        'with_documentation': len(roots) - len(undocumented),
        'without_documentation': len(undocumented),
        'without_documentation_roots': undocumented,
        'array_index_entries': array_entries,
    }


def main():
    ap = argparse.ArgumentParser(description='Build station_identity.json for a predict run')
    ap.add_argument('--run', required=True, help='predict run directory')
    ap.add_argument('--candidates', default=None, help='override candidate pairs CSV path')
    a = ap.parse_args()
    identity, audit = build_identity(a.run, a.candidates)
    out = os.path.join(a.run, 'station_identity.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(identity, f, ensure_ascii=False, indent=1)
    print(f'station_identity: {len(identity)} roots '
          f'({audit["with_documentation"]} documented, '
          f'{audit["without_documentation"]} undocumented -> review) -> {out}')
    if audit['without_documentation_roots']:
        print('  undocumented roots:', ', '.join(audit['without_documentation_roots']))


if __name__ == '__main__':
    main()
