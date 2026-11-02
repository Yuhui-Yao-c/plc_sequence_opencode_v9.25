# -*- coding: utf-8 -*-
"""对 prediction_candidate_pairs.csv（包mylar候选对全量，含原始条件=guards）
用修复后的评测器生成对比工作簿。"""
import sys, csv, hashlib
from pathlib import Path

sys.path.insert(0, r'E:\users\yaoyuhui\步序采集程序备份\独立测试')
sys.stdout.reconfigure(encoding='utf-8')
import gen_coverage_workbook as G
import openpyxl

IND = Path(r'E:\users\yaoyuhui\步序采集程序备份\独立测试')
CAND = IND / 'prediction_candidate_pairs.csv'
MANUAL = Path(r'E:\users\yaoyuhui\步序采集程序备份\包mylar测试'
              r'\步序时长采集-XM2-L5-包mylar-20260913.xlsx')
OUT = IND / 'PLC_Manual_vs_Prediction_Side_by_Side_独立测试_候选对.xlsx'


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest()[:16]


def read_candidate_pairs(path):
    """candidate_pairs -> (records, edges)；guards=原始条件按顶层AND拆分"""
    records, edges = [], []
    with open(path, encoding='utf-8-sig', newline='') as fh:
        for row in csv.DictReader(fh):
            st = (row.get('开始标签') or '').strip()
            en = (row.get('结束标签') or '').strip()
            cond = (row.get('原始条件') or '').strip()
            no = len(records) + 1
            guards = []
            if cond:
                for part in G._split_top_and(cond):
                    gn = G.norm(part)
                    if gn and gn not in guards:
                        guards.append(gn)
            records.append((no, st, en, guards))
            if st and en:
                edges.append((no, st, en))
    return records, edges


def build_extraction_sheet(wb, csv_path):
    """候选对全量步序提取表（关键10列，格式对齐三工序版）。"""
    with open(csv_path, encoding='utf-8-sig', newline='') as fh:
        rows = list(csv.DictReader(fh))
    ws = wb.create_sheet('包mylar候选对 全量步序提取')
    headers = ['#', '候选ID', '状态', '构成要素', '动作描述', '开始标签', '结束标签',
               '原始条件（guards）', '边界规范化状态', '边界转换说明']
    widths = {'A': 7, 'B': 11, 'C': 24, 'D': 18, 'E': 26, 'F': 30, 'G': 30,
              'H': 26, 'I': 18, 'J': 34}
    G.sheet_scaffold(ws, '候选对全量步序提取｜完整记录',
                     '独立测试程序输出的全部候选对（未经人工表干预）；'
                     '原始条件列作为 guards 参与信号核验；'
                     '编号与「逐条对比」表中的预测编号 #n 一一对应。'
                     'state_exit=离开型等待边；observed_same_network_transition=同网络观测转移；'
                     'adjacency/feedback_hypothesis=假设边。',
                     widths, 'J')
    G.header_row(ws, 6, headers)
    r = 7
    for k, row in enumerate(rows, 1):
        vals = [k,
                (row.get('候选ID') or '').strip(),
                (row.get('状态') or '').strip(),
                (row.get('构成要素') or '').strip(),
                (row.get('动作描述') or '').strip()[:80],
                (row.get('开始标签') or '').strip(),
                (row.get('结束标签') or '').strip(),
                (row.get('原始条件') or '').strip(),
                (row.get('边界规范化状态') or '').strip(),
                (row.get('边界转换说明') or '').strip()[:70]]
        G.result_row(ws, r, vals, 'FFFFFFFF' if k % 2 else 'FFF2F6FA', 10,
                     center_cols=(1, 2, 3))
        r += 1
    ws.freeze_panes = 'A7'
    return len(rows)


def main():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    manual_rows = G.read_manual_mylar(str(MANUAL))
    records, edges = read_candidate_pairs(str(CAND))
    print(f'人工 {len(manual_rows)} 行 | 候选记录 {len(records)} 条 | 图边 {len(edges)} 条')
    cnt, root_cnt, n_man = G.build_process_sheets(
        wb, '包mylar候选对', 'm_nNextAutoStep', records, edges, manual_rows,
        str(CAND), str(MANUAL), sha256(CAND), sha256(MANUAL),
        pred_source_desc=('独立测试候选对提取（prediction_candidate_pairs.csv）｜'
                          f'{len(records)}条候选记录；{len(edges)}条完整标签对；'
                          f'原始条件列作为guards参与信号核验'),
        gen_method_desc='步序采集工具自动生成｜数据源为候选对全量输出；匹配口径与参考版一致（含错杀修复：无guards降级/反向NOT等待型）')
    print('判定分布:', dict(cnt))
    print('根因分布:', dict(sorted(root_cnt.items(), key=lambda x: -x[1])))
    n_ext = build_extraction_sheet(wb, str(CAND))
    print(f'全量提取表: {n_ext} 条')
    wb.save(str(OUT))
    print(f'\n已保存: {OUT}')


if __name__ == '__main__':
    main()
