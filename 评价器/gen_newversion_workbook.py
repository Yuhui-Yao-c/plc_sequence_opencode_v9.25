# -*- coding: utf-8 -*-
"""新版独立测试评价：final_prediction_pairs_{mylar,超声波,转接片}.csv
用修复后评测器与人工表对比，生成与三工序版同格式的工作簿。"""
import sys, csv, hashlib
from pathlib import Path

sys.path.insert(0, r'E:\users\yaoyuhui\步序采集程序备份\独立测试')
sys.stdout.reconfigure(encoding='utf-8')
import gen_coverage_workbook as G
import openpyxl

IND = Path(r'E:\users\yaoyuhui\步序采集程序备份\独立测试\新版独立测试')
ROOT = G.ROOT
OUT = IND / 'PLC_Manual_vs_Prediction_Side_by_Side_新版独立测试.xlsx'

PROCS = [
    ('包mylar', 'm_nNextAutoStep',
     (IND / 'final_prediction_pairs_mylar.csv',
      ROOT / '包mylar测试' / '步序时长采集-XM2-L5-包mylar-20260913.xlsx', 'mylar')),
    ('超声', 'STxx.AutoStep',
     (IND / 'final_prediction_pairs_超声波.csv',
      ROOT / '超声测试' / '步序时长采集-XM2-L5-超声波-20260910.xls', 'codesys')),
    ('转接片', 'EMxx.Process_steps',
     (IND / 'final_prediction_pairs_转接片.csv',
      ROOT / '转接片测试' / '4.厦门2转接片 22PPM转接片焊接机步序采集表-20260905步序拆解(已自动还原).xls',
      'codesys')),
]


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest()[:16]


def read_pairs_csv(csv_path):
    """新版原子对 -> (records, edges)；新版无 guards 列。"""
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


def build_extraction_sheet(wb, proc_name, csv_path):
    """新版原子对全量步序提取表（关键10列，格式对齐三工序版）。"""
    with open(csv_path, encoding='utf-8-sig', newline='') as fh:
        rows = list(csv.DictReader(fh))
    ws = wb.create_sheet(f'{proc_name} 全量步序提取')
    headers = ['#', '原子对ID', '构成要素', '动作描述', '开始标签', '结束标签',
               '证据等级', '计时使用方式', '来源候选ID', '说明']
    widths = {'A': 7, 'B': 11, 'C': 18, 'D': 26, 'E': 30, 'F': 30,
              'G': 30, 'H': 34, 'I': 12, 'J': 30}
    G.sheet_scaffold(ws, '新版原子对全量步序提取｜完整记录',
                     '新版独立测试程序输出的全部原子对（未经人工表干预，边界已规范化）；'
                     '编号与「逐条对比」表中的预测编号 #n 一一对应。'
                     '证据等级：源码局部迁移=源码集合首出口-局部条件；原始单对=路径未证明。',
                     widths, 'J')
    G.header_row(ws, 6, headers)
    r = 7
    for k, row in enumerate(rows, 1):
        vals = [k,
                (row.get('原子对ID') or '').strip(),
                (row.get('构成要素') or '').strip(),
                (row.get('动作描述') or '').strip()[:80],
                (row.get('开始标签') or '').strip(),
                (row.get('结束标签') or '').strip(),
                (row.get('证据等级') or '').strip(),
                (row.get('计时使用方式') or '').strip(),
                (row.get('来源候选ID') or '').strip(),
                (row.get('说明') or '').strip()[:70]]
        G.result_row(ws, r, vals, 'FFFFFFFF' if k % 2 else 'FFF2F6FA', 10,
                     center_cols=(1, 2))
        r += 1
    ws.freeze_panes = 'A7'
    return len(rows)


def main():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    summary = []
    for proc_name, var_desc, (csv_file, man_file, kind) in PROCS:
        print(f'===== {proc_name} =====')
        if kind == 'mylar':
            manual_rows = G.read_manual_mylar(str(man_file))
            excluded = []
        else:
            manual_rows, excluded = G.read_manual_codesys(str(man_file))
        records, edges = read_pairs_csv(str(csv_file))
        print(f'人工 {len(manual_rows)} 行（排除总时长统计行 {len(excluded)}）| '
              f'新版原子对 {len(records)} 条 | 图边 {len(edges)} 条')
        cnt, root_cnt, n_man = G.build_process_sheets(
            wb, proc_name, var_desc, records, edges, manual_rows,
            str(csv_file), str(man_file), sha256(csv_file), sha256(man_file),
            excluded=excluded,
            pred_source_desc=(f'新版独立测试原子对提取（{Path(csv_file).name}）｜'
                              f'{len(records)}条原子对；{len(edges)}条完整标签对；'
                              '无guards列，信号核验按无使能处理'),
            gen_method_desc=('步序采集工具自动生成｜数据源为新版原子对输出；'
                             '匹配口径与参考版一致（含错杀修复：无guards降级/反向NOT等待型）'))
        print('判定分布:', dict(cnt))
        print('根因分布:', dict(sorted(root_cnt.items(), key=lambda x: -x[1])))
        n_ext = build_extraction_sheet(wb, proc_name, str(csv_file))
        print(f'全量步序提取表: {n_ext} 条')
        cov = cnt.get('直接匹配', 0) + cnt.get('多段可合并', 0) + cnt.get('已覆盖·写法不一致', 0)
        summary.append((proc_name, n_man, cov, cnt.get('未覆盖', 0)))
    wb.save(str(OUT))
    print(f'\n已保存: {OUT}')
    print('\n===== 总覆盖率 =====')
    for proc, n, cov, unc in summary:
        print(f'  {proc}: 覆盖 {cov}/{n} = {cov / n * 100:.1f}%（未覆盖 {unc}）')


if __name__ == '__main__':
    main()
