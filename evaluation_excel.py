"""Deterministic evaluation workbook, no candidate mutation."""
import csv,json
from pathlib import Path
from artifact_tool import Workbook, SpreadsheetFile

def load(p):
 with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def add_table(wb,name,rows,max_rows=15000):
 sheet=wb.worksheets.add(name)
 if not rows:return
 keys=list(rows[0]);matrix=[keys]+[[str(r.get(k,'') or '')[:3000] for k in keys] for r in rows[:max_rows]]
 n=len(matrix);m=len(keys)
 sheet.get_range_by_indexes(0,0,n,m).values=matrix
 head=sheet.get_range_by_indexes(0,0,1,m)
 head.format.fill='#17324D';head.format.font.color='#FFFFFF';head.format.font.bold=True
 sheet.get_range_by_indexes(0,0,n,m).format.column_width=23
 sheet.get_range_by_indexes(0,0,n,m).format.row_height=48
 sheet.get_range_by_indexes(0,0,n,m).format.wrap_text=True
 sheet.get_range_by_indexes(1,0,max(1,n-1),m).format.vertical_alignment='top'
 head.format.row_height=32
 sheet.freeze_panes.freeze_rows(1)
 if name not in ('指标','汇总','说明'):
  status_key='类别' if name in ('候选命中详情','原子分支诊断') or name.endswith('候选详情') else '最佳匹配'
  status_idx=keys.index(status_key) if status_key in keys else -1
  palette={'严格起止命中':'#DCFCE7','布尔结构命中':'#DCFCE7',
   '状态模型边界命中':'#DDEBFF','GT写法待核验':'#FCE4D6','有条件的迁移替代':'#DBEAFE','一对一匹配冲突':'#FCE7F3',
   '同起点/终点不同':'#FEF3C7','同终点/起点不同':'#FEF3C7',
   '终点状态原子相关':'#F3F4F6','起点状态原子相关':'#F3F4F6',
   '终点反馈信号相关':'#F3F4F6','没有检索到相关候选':'#FEE2E2','复合分支齐全-仅诊断':'#EDE9FE','复合分支部分命中':'#FEF3C7','复合语义待核验':'#FFEDD5','分支已有预测-仅诊断':'#DBEAFE','分支缺失':'#FEE2E2'}
  if status_idx>=0:
   for category,color in palette.items():
    # Conditional formatting keeps per-row colors consistent even after sorting / editing.
    # Excel formula uses status column letter anchored to the column, relative row.
    number=status_idx+1;letter=''
    while number:
     number,rem=divmod(number-1,26);letter=chr(65+rem)+letter
    esc=category.replace('"','""')
    sheet.get_range_by_indexes(1,0,max(1,n-1),m).conditional_formats.add_custom(
       f'=${letter}2="{esc}"',{'fill':color})
  # Static row fills are intentionally used: supported across Excel readers and
  # reliably survive artifact_tool export, unlike conditional formatting in some builds.
  if status_idx>=0:
   for row_no,item in enumerate(rows[:max_rows],1):
    color=palette.get(str(item.get(status_key,'')))
    if color:sheet.get_range_by_indexes(row_no,0,1,m).format.fill=color
  if status_key=='最佳匹配':
   for key,width in [('动作描述',32),('GT开始',38),('GT结束',44),('证据解释',55),('唯一匹配预测开始',38),('唯一匹配预测结束',44)]:
    if key in keys:sheet.get_range_by_indexes(0,keys.index(key),n,1).format.column_width=width
 return sheet
def write_excel(summary,hits,metrics,out):
 wb=Workbook.create();m=json.loads(Path(metrics).read_text(encoding='utf-8'))
 add_table(wb,'指标',[{'指标':k,'数值':json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else str(v)} for k,v in m.items()])
 add_table(wb,'GT逐条评价',load(summary));add_table(wb,'候选命中详情',load(hits))
 branch_path=Path(summary).parent/'gt_atomic_branch_diagnostics.csv'
 if branch_path.exists():add_table(wb,'原子分支诊断',load(branch_path))
 SpreadsheetFile.export_xlsx(wb).save(str(out))
if __name__=='__main__':
 import sys
 write_excel(*sys.argv[1:])
