"""Export v6 benchmark JSON files as one colorful, source-traceable workbook.
Uses artifact_tool; no answer data is used during prediction generation.
"""
import argparse,json
from pathlib import Path
from artifact_tool import Workbook,SpreadsheetFile
HEADERS=['人工编号','人工动作描述','人工开始标签','人工结束标签','预测开始标签（按段）','预测结束标签（按段）','对比结果','对比说明','预测原文件行号','人工原文件行号','分段表位置']
PALETTE={'直接匹配':'#DCFCE7','退出边界等价':'#D1FAE5','多段候选路径':'#DBEAFE','周期待确认':'#FEF3C7','未覆盖':'#FEE2E2'}
def export(items,out):
 wb=Workbook.create();summary=wb.worksheets.add('汇总');data=[]
 for name,file,mode in items:
  report=json.loads(Path(file).read_text(encoding='utf8'));metrics=report['metrics'];c=metrics['counts'];total=metrics['truth_rows'];supported=metrics['supported_label_or_hypothesis']
  data.append([name,mode,total,metrics['prediction_rows'],c.get('直接匹配',0),c.get('退出边界等价',0),c.get('多段候选路径',0),c.get('周期待确认',0),c.get('未覆盖',0),round(supported/total*100,2) if total else 0])
  sheet=wb.worksheets.add(name)
  sheet.get_range('A1:K1').merge_cells() if False else None
  sheet.merge_cells('A1:K1');sheet.get_range('A1').values=[[name+' | 人工与预测逐条对照']]
  sheet.get_range('A2:K2').values=[HEADERS]
  records=report['records'];matrix=[[str(r.get(h,'')) for h in HEADERS] for r in records]
  if matrix:sheet.get_range_by_indexes(2,0,len(matrix),11).values=matrix
  sheet.get_range('A1:K1').format={'fill':'#163C60','font':{'bold':True,'color':'#FFFFFF'},'row_height':30}
  sheet.get_range('A2:K2').format={'fill':'#24557D','font':{'bold':True,'color':'#FFFFFF'},'row_height':30,'wrap_text':True}
  widths=[13,38,37,38,38,38,19,52,21,19,16]
  for col,width in enumerate(widths):sheet.get_range_by_indexes(0,col,len(matrix)+2,1).format.column_width=width
  sheet.freeze_panes.freeze_rows(2)
  sheet.get_range_by_indexes(2,0,len(matrix),11).format.row_height=32 if matrix else 18
  sheet.get_range_by_indexes(2,0,len(matrix),11).format.wrap_text=True if matrix else False
  # Group contiguous statuses for faster styling and preserve original gold order.
  i=0
  while i<len(records):
   status=records[i]['对比结果'];j=i+1
   while j<len(records) and records[j]['对比结果']==status:j+=1
   sheet.get_range_by_indexes(i+2,6,j-i,1).format.fill=PALETTE.get(status,'#FFFFFF')
   i=j
 summary.get_range('A1:J1').values=[['项目','测试来源','人工条数','候选条数','直接匹配','退出边界等价','多段候选路径','待确认','未覆盖','宽松边界支持(%)']]
 summary.get_range('A1:J1').format={'fill':'#163C60','font':{'bold':True,'color':'#FFFFFF'},'row_height':30}
 summary.get_range_by_indexes(1,0,len(data),10).values=data
 summary.get_range('A:A').format.column_width=18;summary.get_range('B:B').format.column_width=35
 summary.get_range('C:J').format.column_width=20
 summary.freeze_panes.freeze_rows(1)
 summary.get_range('A6').values=[['口径说明：直接匹配=标签一致；退出等价=逻辑边界形式；多段=同网络候选路径（未经可达性证明）。均非物理动作召回率。']]
 summary.get_range('A7').values=[['前两个项目为历史缓存候选评估，不能当作v6对原XML的端到端运行结果。']]
 SpreadsheetFile.export_xlsx(wb).save(str(out))
 return data
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--project',action='append',nargs=3,metavar=('名称','报告JSON','来源'),required=True);a=p.parse_args();print(export(a.project,a.output))
