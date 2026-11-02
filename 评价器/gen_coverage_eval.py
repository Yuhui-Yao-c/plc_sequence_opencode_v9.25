# -*- coding: utf-8 -*-
"""覆盖对比评价器（参数化入口）

在预测 finish 完成后，若存在人工采集表（xls/xlsx），用 gen_coverage_workbook
的匹配引擎（match_one/find_path/classify 体系）对预测表与人工表逐条对比，
生成彩色覆盖对比工作簿。

用法:
  python 评价器/gen_coverage_eval.py \
      --prediction outputs/超声波/final_prediction_pairs.csv \
      --manual    source/超声/步序时长采集-XM2-L5-超声波-20260910.xls \
      --out       outputs/超声波_评价/PLC_Manual_vs_Prediction_超声波_final.xlsx \
      --proc-name 超声 --var-desc STxx.AutoStep --kind codesys

参数:
  --prediction  预测CSV（final_prediction_pairs.csv / prediction_candidate_pairs.csv
                / final_sequence.csv / final_process_steps.csv 均可）
  --manual      人工采集表（.xls codesys / .xlsx mylar）
  --out         输出 xlsx 路径
  --proc-name   工序名（默认从人工表文件名推断，如 超声/包mylar/转接片）
  --var-desc    变量描述（默认 STxx.AutoStep；包mylar 用 m_nNextAutoStep）
  --kind        人工表格式 codesys|mylar|auto（默认 auto：.xls→codesys, .xlsx→mylar）

匹配口径与 gen_coverage_workbook 一致：
  直接匹配 / 多段可合并 / 已覆盖·写法不一致 / 已覆盖·拆分粒度不同 /
  周期待确认 / 未覆盖（9类根因）
预测CSV 若含"原始条件"列，则作为 guards 参与信号核验；否则按无使能处理。
"""
import sys
import csv
import hashlib
import argparse
from pathlib import Path

# 引入同目录的 gen_coverage_workbook 引擎
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding='utf-8')
import gen_coverage_workbook as G
import openpyxl


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest()[:16]


def read_pairs_csv(csv_path):
    """预测CSV -> (records, edges)。
    若存在"原始条件"列则提取为 guards（按顶层AND拆分），否则 guards=[]。
    records = (no, start, end, guards)
    返回 (records, edges, has_guards)
    """
    records, edges = [], []
    has_guards = False
    with open(csv_path, encoding='utf-8-sig', newline='') as fh:
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
                if guards:
                    has_guards = True
            records.append((no, st, en, guards))
            if st and en:
                edges.append((no, st, en))
    return records, edges, has_guards


def build_extraction_sheet(wb, proc_name, csv_path):
    """全量步序提取表。列根据预测CSV实际拥有的字段动态选择。"""
    with open(csv_path, encoding='utf-8-sig', newline='') as fh:
        rows = list(csv.DictReader(fh))
    ws = wb.create_sheet(f'{proc_name} 全量步序提取')
    # 动态选择列：优先显示通用列，原始条件存在时单独列出
    has_cond = bool(rows and any((r.get('原始条件') or '').strip() for r in rows[:200]))
    if has_cond:
        headers = ['#', '候选ID/原子对ID', '状态', '构成要素', '动作描述',
                   '开始标签', '结束标签', '原始条件（guards）',
                   '边界规范化状态', '说明']
        keys = ['候选ID', '原子对ID', '状态', '构成要素', '动作描述',
                '开始标签', '结束标签', '原始条件', '边界规范化状态', '说明']
        widths = {'A': 7, 'B': 14, 'C': 22, 'D': 18, 'E': 26,
                  'F': 30, 'G': 30, 'H': 30, 'I': 18, 'J': 34}
    else:
        headers = ['#', '原子对ID', '构成要素', '动作描述', '开始标签',
                   '结束标签', '证据等级', '计时使用方式', '来源候选ID', '说明']
        keys = ['原子对ID', '构成要素', '动作描述', '开始标签',
                '结束标签', '证据等级', '计时使用方式', '来源候选ID', '说明']
        widths = {'A': 7, 'B': 14, 'C': 18, 'D': 26, 'E': 30,
                  'F': 30, 'G': 30, 'H': 34, 'I': 14, 'J': 30}
    G.sheet_scaffold(
        ws, '预测全量步序提取｜完整记录',
        '预测程序输出的全部记录（未经人工表干预）；'
        '编号与「逐条对比」表中的预测编号 #n 一一对应。'
        + ('原始条件列作为 guards 参与信号核验。' if has_cond
           else '无guards列，信号核验按无使能处理。'),
        widths, 'J')
    G.header_row(ws, 6, headers)
    r = 7
    for k, row in enumerate(rows, 1):
        vals = [k]
        for key in keys:
            v = (row.get(key) or '').strip()
            # 动作描述/说明 截断避免过长
            if key in ('动作描述', '说明', '边界转换说明'):
                v = v[:80]
            vals.append(v)
        G.result_row(ws, r, vals, 'FFFFFFFF' if k % 2 else 'FFF2F6FA', 10,
                     center_cols=(1, 2))
        r += 1
    ws.freeze_panes = 'A7'
    return len(rows)


def infer_proc_name(manual_path):
    """从人工表文件名推断工序名"""
    name = Path(manual_path).stem
    for key in ('超声', '超声波', '包mylar', 'mylar', '转接片'):
        if key in name:
            return '超声' if key in ('超声', '超声波') else key
    return name


def infer_kind(manual_path, kind_arg):
    if kind_arg != 'auto':
        return kind_arg
    p = str(manual_path)
    suffix = Path(manual_path).suffix.lower()
    # peek at sheet names to distinguish codesys-style (时序拆解) from mylar (动作拆解)
    try:
        if suffix == '.xlsx':
            import openpyxl as _opx
            wb = _opx.load_workbook(p, read_only=True)
            names = ' '.join(wb.sheetnames)
            wb.close()
            if '时序拆解' in names or '拆解' in names and '动作拆解' not in names:
                return 'codesys'
            return 'mylar'
    except Exception:
        pass
    return 'mylar' if suffix == '.xlsx' else 'codesys'


def main_with(pred, man, out, proc_name, var_desc, kind):
    """Engine entry usable from plc_cli.coverage (no argparse)."""
    pred = Path(pred)
    man = Path(man)
    out = Path(out)
    if not pred.exists():
        print(f'错误: 预测文件不存在: {pred}', file=sys.stderr)
        return
    if not man.exists():
        print(f'错误: 人工表不存在: {man}', file=sys.stderr)
        return
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f'===== {proc_name} 覆盖对比评价 =====')
    print(f'预测: {pred}')
    print(f'人工: {man}（格式: {kind}）')
    print(f'输出: {out}')

    # 读人工表
    if kind == 'mylar':
        manual_rows, excluded = G.read_manual_mylar(str(man)), []
    else:
        manual_rows, excluded = G.read_manual_codesys(str(man))

    # 读预测
    records, edges, has_guards = read_pairs_csv(str(pred))
    print(f'人工 {len(manual_rows)} 行（排除总时长统计行 {len(excluded)}）| '
          f'预测记录 {len(records)} 条 | 图边 {len(edges)} 条'
          f'（含guards: {has_guards}）')

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    cnt, root_cnt, n_man = G.build_process_sheets(
        wb, proc_name, var_desc, records, edges, manual_rows,
        str(pred), str(man), sha256(pred), sha256(man),
        excluded=excluded,
        pred_source_desc=(f'预测提取（{pred.name}）｜'
                          f'{len(records)}条记录；{len(edges)}条完整标签对；'
                          + ('原始条件列作为guards参与信号核验' if has_guards
                             else '无guards列，信号核验按无使能处理')),
        gen_method_desc=('步序采集工具自动生成｜匹配口径与参考版一致'
                         '（含错杀修复：无guards降级/反向NOT等待型）'))
    print('判定分布:', dict(cnt))
    print('根因分布:', dict(sorted(root_cnt.items(), key=lambda x: -x[1])))
    n_ext = build_extraction_sheet(wb, proc_name, str(pred))
    print(f'全量步序提取表: {n_ext} 条')

    cov = (cnt.get('直接匹配', 0) + cnt.get('多段可合并', 0)
           + cnt.get('已覆盖·写法不一致', 0) + cnt.get('已覆盖·拆分粒度不同', 0))
    unc = cnt.get('未覆盖', 0)
    print(f'\n===== 覆盖率 =====')
    print(f'  {proc_name}: 覆盖 {cov}/{n_man} = {cov / n_man * 100:.1f}%（未覆盖 {unc}）')

    wb.save(str(out))
    print(f'\n已保存: {out}')
    return {'covered': cov, 'total': n_man, 'uncovered': unc, 'cnt': dict(cnt), 'root_cnt': dict(root_cnt)}


def main():
    ap = argparse.ArgumentParser(description='覆盖对比评价器（参数化入口）')
    ap.add_argument('--prediction', required=True, help='预测CSV路径')
    ap.add_argument('--manual', required=True, help='人工采集表 xls/xlsx')
    ap.add_argument('--out', required=True, help='输出 xlsx 路径')
    ap.add_argument('--proc-name', default=None, help='工序名（默认从文件名推断）')
    ap.add_argument('--var-desc', default='STxx.AutoStep', help='变量描述')
    ap.add_argument('--kind', default='auto',
                    choices=['auto', 'codesys', 'mylar'], help='人工表格式')
    args = ap.parse_args()

    pred = Path(args.prediction)
    man = Path(args.manual)
    proc_name = args.proc_name or infer_proc_name(man)
    kind = infer_kind(man, args.kind)
    var_desc = args.var_desc
    main_with(pred, man, Path(args.out), proc_name, var_desc, kind)


if __name__ == '__main__':
    main()
