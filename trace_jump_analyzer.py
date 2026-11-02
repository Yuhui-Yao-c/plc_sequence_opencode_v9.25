# -*- coding: utf-8 -*-
"""Trace jump analysis: parse TwinCAT Scope .trace files and match predicted
state pairs to observed runtime jumps, yielding measured durations.

Reads only the trace file + prediction CSV; never opens GT/manual tables.
Does not generate/modify predictions. Outputs CSV/JSON beside the prediction.
"""
import csv
import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict


# ---------- Trace parsing ----------

def parse_trace(trace_path):
    """Parse a TwinCAT Scope View .trace XML (UTF-16) file.

    Returns dict: var_name -> {
        'values': [float,...], 'timestamps': [float,...],
        'jumps': [(idx, from_val, to_val, ts_ms), ...],
        'segments': [(value, start_ts, end_ts, duration_ms), ...],
        'sample_count': int, 'time_range': (first_ts, last_ts),
        'inactive': bool, 'unique_values': [float,...]
    }
    """
    with open(trace_path, 'r', encoding='utf-16') as f:
        txt = f.read()

    # Locate each <TraceVariable>...</TraceVariable> block.
    starts = [m.start() for m in re.finditer(r'<TraceVariable\b', txt)]
    ends = [m.start() for m in re.finditer(r'</TraceVariable>', txt)]
    if len(starts) != len(ends):
        raise ValueError(f'TraceVariable 开始/结束标签数不匹配: {len(starts)} vs {len(ends)}')

    variables = {}
    for i in range(len(starts)):
        block = txt[starts[i]:ends[i] + len('</TraceVariable>')]
        name_m = re.search(r'VarName="([^"]+)"', block)
        if not name_m:
            continue
        name = name_m.group(1)
        vals_m = re.search(r'<Values>(.*?)</Values>', block, re.DOTALL)
        ts_m = re.search(r'<Timestamps>(.*?)</Timestamps>', block, re.DOTALL)
        if not vals_m or not ts_m:
            continue
        try:
            values = [float(v) for v in vals_m.group(1).split(',') if v.strip()]
            timestamps = [float(t) for t in ts_m.group(1).split(',') if t.strip()]
        except ValueError as e:
            print(f'trace 变量 {name} 数值解析失败: {e}', file=sys.stderr)
            continue
        if len(values) != len(timestamps):
            print(f'trace 变量 {name} 值数({len(values)})≠时间戳数({len(timestamps)})，跳过', file=sys.stderr)
            continue
        # jumps
        jumps = []
        for j in range(1, len(values)):
            if values[j] != values[j - 1]:
                jumps.append((j, values[j - 1], values[j], timestamps[j]))
        # segments
        segments = []
        seg_start = 0
        for j in range(1, len(values)):
            if values[j] != values[j - 1]:
                segments.append((values[seg_start], timestamps[seg_start], timestamps[j],
                                 timestamps[j] - timestamps[seg_start]))
                seg_start = j
        if seg_start < len(values):
            segments.append((values[seg_start], timestamps[seg_start], timestamps[-1],
                             timestamps[-1] - timestamps[seg_start]))
        unique_vals = sorted(set(values))
        inactive = (len(jumps) == 0)
        variables[name] = {
            'values': values,
            'timestamps': timestamps,
            'jumps': jumps,
            'segments': segments,
            'sample_count': len(values),
            'time_range': (timestamps[0] if timestamps else 0, timestamps[-1] if timestamps else 0),
            'inactive': inactive,
            'unique_values': unique_vals,
        }
    return variables


# ---------- Cycle detection ----------

def detect_cycles(variables, idle_threshold_ms=5000):
    """Detect production cycle boundaries from long idle (value=0) segments.

    Excludes variables that never have value 0 (continuous runners like EM13V).
    Returns list of (boundary_ts, var_name) sorted by ts.
    """
    boundaries = []
    for name, v in variables.items():
        if v['inactive']:
            continue
        for value, s_ts, e_ts, dur in v['segments']:
            if value == 0.0 and dur >= idle_threshold_ms:
                boundaries.append((e_ts, name))  # boundary = end of idle
    boundaries.sort()
    return boundaries


# ---------- Prediction matching ----------

_LABEL_RE = re.compile(r'\s*([A-Za-z_]\w*(?:\[[^\]]*\])?(?:\.\w+(?:\[[^\]]*\])?)*)\s*=\s*([+-]?\d+(?:\.\d+)?)\s*$')


def parse_label(label):
    """Parse 'Var.Name[idx]=123' -> (var_name, value_float).
    Returns (None, None) for BOOL labels without '='."""
    m = _LABEL_RE.match(str(label))
    if not m:
        return None, None
    return m.group(1), float(m.group(2))


def _find_segments_with_value(var_data, value):
    """Return list of (start_ts, end_ts, duration) segments where value matches."""
    return [(s, e, d) for (v, s, e, d) in var_data['segments'] if v == value]


def match_one_pair(var_name, val_start, val_end, variables, via_zero_max_ms=1000):
    """Match a single predicted pair (var, val_start->val_end) against trace.

    Returns list of match dicts, each with:
        match_type: 'exact' | 'via_zero' | None
        start_ts, end_ts, duration_ms
    Empty list = no match.
    """
    if var_name not in variables:
        return []
    vd = variables[var_name]
    if vd['inactive']:
        return []

    # Collect all segments of val_start and val_end in time order.
    # For each val_start segment, find the next val_end segment that starts
    # after val_start segment starts (allowing 0 in between).
    start_segs = _find_segments_with_value(vd, val_start)
    end_segs = _find_segments_with_value(vd, val_end)
    matches = []

    for s_start, s_end, s_dur in start_segs:
        # Find the earliest end segment whose start_ts > s_start (strictly after).
        # val_end segment start_ts is the timestamp where value becomes val_end.
        best_end = None
        best_type = None
        for e_start, e_end, e_dur in end_segs:
            if e_start > s_start:
                # Check if there's a 0 segment between s_end and e_start (via_zero).
                if e_start - s_start <= 1:  # essentially immediate (same sample)
                    pass
                # Direct: end segment immediately follows start segment
                # (end.start_ts == start.end_ts approximately).
                if abs(e_start - s_end) < 50:  # tolerance 50ms
                    best_end = e_start
                    best_type = 'exact'
                    break
                # Via zero: a 0 segment exists between s_end and e_start.
                zero_segs = _find_segments_with_value(vd, 0.0)
                for z_start, z_end, z_dur in zero_segs:
                    if z_start >= s_end - 50 and z_end <= e_start + 50 and z_dur <= via_zero_max_ms:
                        best_end = e_start
                        best_type = 'via_zero'
                        break
                if best_end is not None:
                    break
                # Fallback: if no exact/via_zero found yet, take the nearest end
                # segment after start as a candidate (loose match).
                if best_end is None:
                    best_end = e_start
                    best_type = 'loose'
        if best_end is not None:
            matches.append({
                'match_type': best_type,
                'start_ts': s_start,
                'end_ts': best_end,
                'duration_ms': best_end - s_start,
            })

    return matches


def match_predictions(prediction_csv, variables):
    """Match all prediction pairs against trace jumps.

    Returns (mapping_rows, runtime_rows, review_rows).
    """
    pred_rows = []
    with open(prediction_csv, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        pred_rows = list(reader)

    mapping_rows = []
    runtime_rows = []
    review_rows = []
    seen_trace_jumps = set()  # (var, from, to) to detect trace-only jumps

    for i, row in enumerate(pred_rows):
        start_label = row.get('开始标签', '')
        end_label = row.get('结束标签', '')
        var_s, val_s = parse_label(start_label)
        var_e, val_e = parse_label(end_label)

        if var_s is None or var_e is None:
            review_rows.append({
                '类型': 'non_state_label',
                '开始标签': start_label,
                '结束标签': end_label,
                '说明': '标签非状态等式（BOOL或无=值），无法匹配trace',
            })
            runtime_rows.append({
                '开始标签': start_label,
                '结束标签': end_label,
                '平均时长ms': '',
                '发生次数': 0,
                '匹配类型': 'non_state_label',
                '首次时间ms': '',
                '末次时间ms': '',
                '最小时长ms': '',
                '最大时长ms': '',
            })
            continue

        if var_s != var_e:
            review_rows.append({
                '类型': 'cross_variable_pair',
                '开始标签': start_label,
                '结束标签': end_label,
                '说明': f'跨变量对: {var_s} → {var_e}，trace不匹配',
            })
            runtime_rows.append({
                '开始标签': start_label,
                '结束标签': end_label,
                '平均时长ms': '',
                '发生次数': 0,
                '匹配类型': 'cross_variable_pair',
                '首次时间ms': '',
                '末次时间ms': '',
                '最小时长ms': '',
                '最大时长ms': '',
            })
            continue

        matches = match_one_pair(var_s, val_s, val_e, variables)
        if not matches:
            review_rows.append({
                '类型': 'predicted_but_not_observed',
                '开始标签': start_label,
                '结束标签': end_label,
                '说明': f'{var_s}: {val_s}→{val_e} 在trace中未观察到',
            })
            runtime_rows.append({
                '开始标签': start_label,
                '结束标签': end_label,
                '平均时长ms': '',
                '发生次数': 0,
                '匹配类型': 'not_observed',
                '首次时间ms': '',
                '末次时间ms': '',
                '最小时长ms': '',
                '最大时长ms': '',
            })
            continue

        durations = [m['duration_ms'] for m in matches]
        types = [m['match_type'] for m in matches]
        # Pick the dominant match type
        from collections import Counter as _C
        type_counts = _C(types)
        dominant_type = type_counts.most_common(1)[0][0]

        mapping_rows.append({
            '开始标签': start_label,
            '结束标签': end_label,
            '变量': var_s,
            'from值': val_s,
            'to值': val_e,
            '发生次数': len(matches),
            '匹配类型': dominant_type,
            '首次时间ms': int(matches[0]['start_ts']),
            '末次时间ms': int(matches[-1]['start_ts']),
            '平均时长ms': int(round(sum(durations) / len(durations))),
            '最小时长ms': int(round(min(durations))),
            '最大时长ms': int(round(max(durations))),
        })
        runtime_rows.append({
            '开始标签': start_label,
            '结束标签': end_label,
            '平均时长ms': int(round(sum(durations) / len(durations))),
            '发生次数': len(matches),
            '匹配类型': dominant_type,
            '首次时间ms': int(matches[0]['start_ts']),
            '末次时间ms': int(matches[-1]['start_ts']),
            '最小时长ms': int(round(min(durations))),
            '最大时长ms': int(round(max(durations))),
        })
        seen_trace_jumps.add((var_s, val_s, val_e))

    # Detect trace-only jumps (in trace but not in predictions).
    pred_pairs = set()
    for row in pred_rows:
        var_s, val_s = parse_label(row.get('开始标签', ''))
        var_e, val_e = parse_label(row.get('结束标签', ''))
        if var_s and var_e and var_s == var_e:
            pred_pairs.add((var_s, val_s, val_e))

    for name, vd in variables.items():
        if vd['inactive']:
            continue
        for idx, from_v, to_v, ts in vd['jumps']:
            if (name, from_v, to_v) not in pred_pairs:
                review_rows.append({
                    '类型': 'trace_only_jump',
                    '开始标签': f'{name}={int(from_v) if from_v == int(from_v) else from_v}',
                    '结束标签': f'{name}={int(to_v) if to_v == int(to_v) else to_v}',
                    '说明': f'trace观察到 {name}: {from_v}→{to_v} @ {int(ts)}ms，预测无此对',
                })

    return mapping_rows, runtime_rows, review_rows


# ---------- Orchestration ----------

def analyze(trace_path, prediction_csv, out_dir):
    """Run full trace analysis: parse -> match -> write outputs.

    Returns stats dict.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Parse trace.
    variables = parse_trace(trace_path)
    active_count = sum(1 for v in variables.values() if not v['inactive'])
    inactive_count = sum(1 for v in variables.values() if v['inactive'])

    # 2. Write trace_variables.json.
    var_meta = {}
    for name, vd in variables.items():
        var_meta[name] = {
            'sample_count': vd['sample_count'],
            'time_range_ms': [int(vd['time_range'][0]), int(vd['time_range'][1])],
            'inactive': vd['inactive'],
            'jump_count': len(vd['jumps']),
            'unique_values': [int(x) if x == int(x) else x for x in vd['unique_values']],
        }
    (out_dir / 'trace_variables.json').write_text(
        json.dumps(var_meta, ensure_ascii=False, indent=2), encoding='utf-8')

    # 3. Write trace_jumps.csv.
    jump_fields = ['变量', 'from值', 'to值', '时间戳ms', '样本索引']
    jump_rows = []
    for name, vd in variables.items():
        for idx, from_v, to_v, ts in vd['jumps']:
            jump_rows.append({
                '变量': name,
                'from值': int(from_v) if from_v == int(from_v) else from_v,
                'to值': int(to_v) if to_v == int(to_v) else to_v,
                '时间戳ms': int(ts),
                '样本索引': idx,
            })
    _write_csv(out_dir / 'trace_jumps.csv', jump_rows, jump_fields)

    # 4. Write trace_segments.csv.
    seg_fields = ['变量', '值', '起始ms', '结束ms', '时长ms']
    seg_rows = []
    for name, vd in variables.items():
        for value, s_ts, e_ts, dur in vd['segments']:
            seg_rows.append({
                '变量': name,
                '值': int(value) if value == int(value) else value,
                '起始ms': int(s_ts),
                '结束ms': int(e_ts),
                '时长ms': int(dur),
            })
    _write_csv(out_dir / 'trace_segments.csv', seg_rows, seg_fields)

    # 5. Detect cycles.
    cycles = detect_cycles(variables)
    cycle_data = [{'boundary_ts': int(ts), 'var': name} for ts, name in cycles]
    (out_dir / 'trace_cycles.json').write_text(
        json.dumps(cycle_data, ensure_ascii=False, indent=2), encoding='utf-8')

    # 6. Match predictions.
    mapping_rows, runtime_rows, review_rows = match_predictions(prediction_csv, variables)

    # 7. Write mapping, runtime, review CSVs.
    map_fields = ['开始标签', '结束标签', '变量', 'from值', 'to值', '发生次数',
                  '匹配类型', '首次时间ms', '末次时间ms', '平均时长ms', '最小时长ms', '最大时长ms']
    _write_csv(out_dir / 'trace_prediction_mapping.csv', mapping_rows, map_fields)

    rt_fields = ['开始标签', '结束标签', '平均时长ms', '发生次数', '匹配类型',
                 '首次时间ms', '末次时间ms', '最小时长ms', '最大时长ms']
    _write_csv(out_dir / 'trace_step_runtime.csv', runtime_rows, rt_fields)

    rev_fields = ['类型', '开始标签', '结束标签', '说明']
    _write_csv(out_dir / 'trace_review.csv', review_rows, rev_fields)

    matched = sum(1 for r in runtime_rows if r['发生次数'] and r['发生次数'] > 0)
    total = len(runtime_rows)
    stats = {
        'trace_path': str(trace_path),
        'prediction_csv': str(prediction_csv),
        'variables_total': len(variables),
        'variables_active': active_count,
        'variables_inactive': inactive_count,
        'total_jumps': len(jump_rows),
        'total_segments': len(seg_rows),
        'cycles_detected': len(cycle_data),
        'prediction_pairs': total,
        'matched_pairs': matched,
        'unmatched_pairs': total - matched,
        'review_count': len(review_rows),
        'outputs': {
            'variables': str(out_dir / 'trace_variables.json'),
            'jumps': str(out_dir / 'trace_jumps.csv'),
            'segments': str(out_dir / 'trace_segments.csv'),
            'cycles': str(out_dir / 'trace_cycles.json'),
            'mapping': str(out_dir / 'trace_prediction_mapping.csv'),
            'runtime': str(out_dir / 'trace_step_runtime.csv'),
            'review': str(out_dir / 'trace_review.csv'),
        },
    }
    return stats


def load_runtime_map(run_dir):
    """Load trace_step_runtime.csv from a run directory.
    Returns dict: (start_label, end_label) -> average_duration_ms (int or None).
    Returns {} if file doesn't exist (no trace analyzed)."""
    p = Path(run_dir) / 'trace_step_runtime.csv'
    if not p.exists():
        return {}
    rt = {}
    with open(p, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            dur = row.get('平均时长ms', '')
            key = (row.get('开始标签', ''), row.get('结束标签', ''))
            if dur and dur.strip():
                try:
                    rt[key] = int(dur)
                except ValueError:
                    rt[key] = None
            else:
                rt[key] = None
    return rt


def _write_csv(path, rows, fields):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def main():
    import argparse
    ap = argparse.ArgumentParser(description='PLC trace jump analysis')
    ap.add_argument('--trace', required=True, help='.trace 文件路径')
    ap.add_argument('--prediction', required=True, help='final_prediction_pairs.csv 路径')
    ap.add_argument('--out', required=True, help='输出目录')
    a = ap.parse_args()
    stats = analyze(a.trace, a.prediction, a.out)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
