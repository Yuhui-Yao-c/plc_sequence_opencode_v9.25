# -*- coding: utf-8 -*-
"""独立测试：用当前对比器（match_one/find_path/classify 体系）对独立测试目录的
三张提取表（CSV）与人工表对比，生成与正式工作簿相同格式的三工序工作簿。"""
import sys
import csv
import hashlib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r'E:\users\yaoyuhui\步序采集程序备份\独立测试')
import gen_coverage_workbook as G
import openpyxl

IND = Path(r'E:\users\yaoyuhui\步序采集程序备份\独立测试')
ROOT = G.ROOT
OUT = IND / 'PLC_Manual_vs_Prediction_Side_by_Side_独立测试.xlsx'

PROCS = [
    ('包mylar', 'm_nNextAutoStep',
     (IND / 'final_sequence_包Mylar.csv',
      ROOT / '包mylar测试' / '步序时长采集-XM2-L5-包mylar-20260913.xlsx', 'mylar')),
    ('超声', 'STxx.AutoStep',
     (IND / 'final_sequence_超声波.csv',
      ROOT / '超声测试' / '步序时长采集-XM2-L5-超声波-20260910.xls', 'codesys')),
    ('转接片', 'EMxx.Process_steps',
     (IND / 'final_sequence_转接片.csv',
      ROOT / '转接片测试' / '4.厦门2转接片 22PPM转接片焊接机步序采集表-20260905步序拆解(已自动还原).xls',
      'codesys')),
]


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest()[:16]


def read_segments_csv(csv_path):
    """CSV -> (records, edges)；records=(no, start, end, guards=[])，guards 独立提取表未提供。"""
    records, edges = [], []
    with open(csv_path, encoding='utf-8-sig', newline='') as fh:
        for row in csv.DictReader(fh):
            st = (row.get('开始标签') or '').strip()
            en = (row.get('结束标签') or '').strip()
            no = len(records) + 1
            records.append((no, st, en, []))
            if st and en:
                edges.append((no, st, en))
    return records, edges


def build_extraction_sheet_csv(wb, proc_name, csv_path):
    """全量步序提取表（CSV 原文 9 列）。"""
    with open(csv_path, encoding='utf-8-sig', newline='') as fh:
        rows = list(csv.DictReader(fh))
    ws = wb.create_sheet(f'{proc_name} 全量步序提取')
    headers = ['#', '构成要素', '动作', '动作描述', '开始标签', '结束标签',
               '核验状态', '证据候选ID', '证据事件ID', '说明']
    widths = {'A': 7, 'B': 24, 'C': 16, 'D': 26, 'E': 30, 'F': 30,
              'G': 18, 'H': 12, 'I': 16, 'J': 34}
    G.sheet_scaffold(ws, '独立测试全量步序提取｜完整记录',
                     '独立测试程序输出的全部步序记录（未提供 guards，涉及信号核验的判定按无使能处理）；'
                     '编号与「逐条对比」表中的预测编号 #n 一一对应。',
                     widths, 'J')
    G.header_row(ws, 6, headers)
    r = 7
    for k, row in enumerate(rows, 1):
        vals = [k,
                (row.get('构成要素') or '').strip(),
                (row.get('动作') or '').strip(),
                (row.get('动作描述') or '').strip(),
                (row.get('开始标签') or '').strip(),
                (row.get('结束标签') or '').strip(),
                (row.get('核验状态') or '').strip(),
                (row.get('证据候选ID') or '').strip(),
                (row.get('证据事件ID') or '').strip(),
                (row.get('说明') or '').strip()]
        G.result_row(ws, r, vals, 'FFFFFFFF' if k % 2 else 'FFF2F6FA', 10,
                     center_cols=(1, 7, 8, 9))
        r += 1
    ws.freeze_panes = 'A7'
    return len(rows)


def main():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for proc_name, var_desc, (csv_file, man_file, kind) in PROCS:
        print(f'===== {proc_name} =====')
        if kind == 'mylar':
            manual_rows, excluded = G.read_manual_mylar(man_file), []
        else:
            manual_rows, excluded = G.read_manual_codesys(man_file)
        seg_records, seg_edges = read_segments_csv(csv_file)
        print(f'人工 {len(manual_rows)} 行（排除总时长统计行 {len(excluded)}）| '
              f'独立提取记录 {len(seg_records)} 条 | 图边 {len(seg_edges)} 条')
        cnt, root_cnt, n_man = G.build_process_sheets(
            wb, proc_name, var_desc, seg_records, seg_edges, manual_rows,
            str(csv_file), str(man_file), sha256(csv_file), sha256(man_file),
            excluded=excluded)
        print('判定分布:', dict(cnt))
        print('根因分布:', dict(sorted(root_cnt.items(), key=lambda x: -x[1])))
        n_ext = build_extraction_sheet_csv(wb, proc_name, csv_file)
        print(f'全量步序提取表: {n_ext} 条')
    wb.save(str(OUT))
    print(f'\n已保存: {OUT}')


if __name__ == '__main__':
    main()
