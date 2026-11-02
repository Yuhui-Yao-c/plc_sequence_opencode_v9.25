"""Evidence-graded PLC timing-boundary and human-label diagnostic utilities.

Never infer timestamp identity from control-flow co-occurrence. Gold is diagnostic ONLY.
"""
import argparse,csv,json,re,sys
from pathlib import Path
from collections import Counter,defaultdict
STATE = re.compile(r'(?<![\w.])(?P<var>[A-Za-z_\u0080-\uffff][\w.\[\],\u0080-\uffff]*)\s*=\s*(?:(?:INT|DINT|UINT|UDINT|SINT|USINT)#)?(?P<num>\d+)',re.I)
SIGNAL = re.compile(r'(?<![\w.])([A-Za-z_\u0080-\uffff][\w.\[\],\u0080-\uffff]*)')

def atoms(expr):
 return {(m.group('var').lower(),int(m.group('num'))) for m in STATE.finditer(expr or '')}

def read_csv(path):
 csv.field_size_limit(sys.maxsize)
 with open(path,encoding='utf-8-sig',newline='') as f:
  header=f.readline();f.seek(0);return list(csv.DictReader(f,delimiter='\t' if '\t' in header else ','))

def extract_rows(rows):
 # Distinguish feedback-required boundaries from mere state and label evidence.
 out=[]
 for i,row in enumerate(rows,1):
  start=row.get('人工开始标签',row.get('开始标签',''));end=row.get('人工结束标签',row.get('结束标签',''))
  common=atoms(start)|atoms(end)
  residue=STATE.sub(' ',end or '')
  residue=re.sub(r'\b(AND|OR|NOT|TRUE|FALSE|INT|DINT|UINT)\b',' ',residue,flags=re.I)
  signals=[s for s in SIGNAL.findall(residue) if s.lower() not in {'and','or','not','true','false'}]
  out.append({'index':i,'start':start,'end':end,'state_atoms':common,'end_feedback_symbols':signals,'row':row})
 return out

def diagnose(truth_rows,candidates,event_rows=None):
 """Classify failures; 'suspected annotation error' REQUIRES positive contradictory source evidence.
    Candidate-only absence never qualifies as evidence of gold error.
 """
 event_rows=event_rows or []
 state_seen=set(); candidate_pairs=set(); event_symbols=set()
 for r in candidates:
  state_seen|=atoms(r.get('开始标签',''))|atoms(r.get('结束标签',''))
  candidate_pairs.add((canon(r.get('开始标签','')),canon(r.get('结束标签',''))))
 for r in event_rows:
  for col in ('guard','feedback','next_state','arguments','command'):
   event_symbols.update(s.lower() for s in SIGNAL.findall(r.get(col,'') or ''))
 results=[];count=Counter()
 for item in extract_rows(truth_rows):
  s,e=item['start'],item['end'];a=atoms(s); b=atoms(e)
  direct=(canon(s),canon(e)) in candidate_pairs
  missing=a-state_seen
  signal_missing=[x for x in item['end_feedback_symbols'] if x.lower() not in event_symbols]
  same_var_mismatch=bool(a and b and not {x[0] for x in a}&{x[0] for x in b})
  if direct: status='直接候选匹配';reason='候选标签一致，但未证明动作完成或采集时间相同'
  elif missing:status='状态证据缺口';reason='至少一个起点状态未在候选中观察到；也可能是解析器遗漏，不能据此判断人工错误'
  elif signal_missing:status='反馈证据缺口';reason='候选事件图未观察到终点信号：'+', '.join(signal_missing[:5])+'；不能据此判断人工错误'
  elif same_var_mismatch:status='跨控制器关系待核验';reason='起止使用不同状态变量，需信号依赖关系证明'
  else:status='控制流/边界待核验';reason='可见部分状态证据，但缺少可证明的完整边界或路径'
  # NEVER assert annotation error merely because the analyzer fails.
  count[status]+=1
  results.append({'人工编号':item['row'].get('人工编号',item['index']),'人工动作描述':item['row'].get('人工动作描述',item['row'].get('动作描述','')),'人工开始标签':s,'人工结束标签':e,'诊断状态':status,'说明':reason,'疑似人工错误':'未证实','人工错误依据':'无正面矛盾源码证据，不作人工错误结论','待核验反馈':';'.join(signal_missing)})
 return {'metrics':dict(count),'rows':results,'caveat':'候选与事件图不等于完整PLC源码；没有源码控制流反证时，绝不将人工答案标为错误。'}

def canon(s):return re.sub(r'\s+','',re.sub(r'\b(?:INT|DINT|UINT|UDINT)#','',s or '',flags=re.I)).lower()

def timing_relations(events):
 """Conservative local evidence: never emit strict equality; co-location cannot imply verified transition."""
 result=[]
 for i,r in enumerate(events,2):
  guard=r.get('guard','');feedback=r.get('feedback','');next_state=r.get('next_state','')
  if not guard or not next_state:continue
  starts=atoms(guard);ends=atoms(next_state)
  if not any(s[0]==e[0] and s[1]!=e[1] for s in starts for e in ends):continue
  for sig in [s.strip() for s in feedback.split(' AND ') if s.strip()]:
   if any(c in sig for c in '<>=') or not re.fullmatch(r'[\w.\[\],\u0080-\uffff]+',sig):continue
   result.append({'事件源行号':i,'程序单元':r.get('source_unit',''),'网络':r.get('network',''),
    '条件状态':guard,'反馈信号':sig,'目标状态':next_state,
    '反馈边界':guard+' AND '+sig,'迁移边界':next_state,
    '时间关系':'同一网络观察/待核验' if r.get('source_format')=='smc2' else '同程序单元观察/待核验',
    '严格布尔等价':'否','时间戳相等':'未验证',
    '阻碍':'缺少完整使能链、写入先后/扫描取样位置验证',
    '取证要求':'追踪反馈触点至MOVE EN的连接、检查其它赋值路径和采集周期'})
 return result

def output(source,truth=None,out=None):
 p=Path(source);out=Path(out or p.parent/'v8_diagnostics');out.mkdir(parents=True,exist_ok=True)
 cand=read_csv(p);ev_path=p.parent/'event_graph.csv';ev=read_csv(ev_path) if ev_path.exists() else []
 timing=timing_relations(ev)
 write_csv(out/'timing_relations.csv',timing,list(timing[0]) if timing else ['事件源行号','时间关系'])
 summary={'candidates':len(cand),'events':len(ev),'timing_relation_hypotheses':len(timing),'source_only':True}
 if truth:
  gold=read_csv(truth) if str(truth).lower().endswith('.csv') else parse_truth(truth)
  d=diagnose(gold,cand,ev)
  (out/'annotation_diagnosis.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
  write_csv(out/'annotation_diagnosis.csv',d['rows'],list(d['rows'][0]) if d['rows'] else ['人工编号'])
  summary['annotation']=d['metrics'];summary['truth_count']=len(gold)
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');return summary

def parse_truth(path):
 rows=[]
 for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
  if line.strip().startswith('|'):
   cells=[s.strip() for s in line.strip().strip('|').split('|')]
  else:cells=line.split('\t')
  if len(cells)<3 or not cells[1].strip() or not cells[2].strip():continue
  if cells[1].strip() in ('开始标签','开始','---','A') or cells[2].strip() in ('结束标签','结束'):continue
  if set(cells[1].strip())=={'-'}:continue
  rows.append({'人工编号':len(rows)+1,'人工动作描述':cells[0],'人工开始标签':cells[1],'人工结束标签':cells[2]})
 return rows

def write_csv(path,rows,fields):
 with open(path,'w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('candidates');ap.add_argument('--truth');ap.add_argument('-o','--out');a=ap.parse_args();print(json.dumps(output(a.candidates,a.truth,a.out),ensure_ascii=False,indent=2))
