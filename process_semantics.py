"""Generic *prediction-only* process-action evidence triage.
Does not consult GT. Preserve all raw candidates; this ranks likely real process
steps and separates weak state-only hypotheses from final recommendations.
"""
import csv, re, argparse, json
from pathlib import Path
from collections import Counter

MECHANISM = re.compile(r"axis(move|abs|rel)|motor|cylinder|vacuum|servo|robot|motion|夹爪|气缸|轴|机器人|真空|吸|夹紧|移动|下降|上升|旋转|焊接|点胶|激光|切胶|伸出|缩回",re.I)
PROCESS = re.compile(r"视觉|ccd|检测|扫码|扫码枪|测量|测试|读取|通信|握手|等待|放行|允许|ready|done|permit|sensor|mes|拍照|计时|冷却|加热|超时",re.I)
ADMIN = re.compile(r"^(EXECUTE|MOVE|EQ|GT|LT|LE|GE|R_TRIG|AND|OR|NOT|ADD|SUB|TON|CTU|DIVTIME|MULTIME|INT_TO|REAL_TO)$",re.I)
WEAK = {'adjacency_hypothesis','reset_hypothesis','state_exit','feedback_hypothesis'}

def read_rows(path):
 with open(path,encoding='utf-8-sig',newline='') as f:
  sample=f.read(4096);f.seek(0)
  sep='\t' if sample.splitlines() and sample.splitlines()[0].count('\t')>sample.splitlines()[0].count(',') else ','
  return list(csv.DictReader(f,delimiter=sep))

def tag(row):
 status=row.get('状态',''); action=row.get('动作','');desc=row.get('动作描述','');raw=row.get('原始条件','')
 hay=' '.join([action,desc,raw]); signal=bool(MECHANISM.search(hay)); proc=bool(PROCESS.search(hay))
 isweak=status in WEAK or '未确认阶段' in action or '可能结束阶段' in action or action=='待确认'
 if isweak:
  # A comment in a state hypothesis does not certify that the hypothesized edge executed.
  return ('状态假设/待关联动作','review_only','无可验证的动作/检测/工艺等待连接；不直接纳入推荐步序')
 if status=='action_event_unverified':
  if signal:return ('执行机构命令','needs_boundary','已发现可能动作命令，完成条件仍需验证')
  if proc:return ('工艺检测或等待','needs_boundary','存在工艺事件线索，需核验实际运行条件')
  return ('非工艺或未分类事件','review_only','通用工具调用/线圈/赋值不自动作为动作')
 if status=='smc2_wired_wait_transition':
  return ('有局部门控的等待阶段','needs_process_review','独立等待无需机械命令；源网络注释和迁移条件同时保留，仍需工艺复核')
 if 'wired' in status or 'local_control' in status:
  return ('连线支持的状态迁移','needs_action_link','已证实局部使能或迁移，尚需关联动作/检测/等待')
 if status in ('same_network_transition','feedback_guard','transition_guard'):
  return ('工艺迁移线索','needs_action_link','迁移仅作候选，动作语义需独立核验')
 if signal:return ('可能执行机构动作','needs_boundary','需要检查控制流和完成条件')
 if proc:return ('可能检测/等待','needs_boundary','需要检查工艺必要性与条件')
 return ('未分类候选','review_only','不能仅由状态号推断真实工艺动作')

def classify_file(input_path,out):
 out=Path(out);out.mkdir(parents=True,exist_ok=True);rows=read_rows(input_path)
 fields=list(rows[0]) if rows else ['开始标签','结束标签'];fields+=['工艺分类','处理建议','建议依据']
 annotated=[];counts=Counter()
 for r in rows:
  a,b,c=tag(r);r={**r,'工艺分类':a,'处理建议':b,'建议依据':c};annotated.append(r);counts[b]+=1
 # State seeds are NOT steps. Feed unique EQ-like state observations back to the
 # XML connectivity explorer, so PLCopen projects are not ignored when their
 # existing candidates contain only adjacency hypotheses.
 seeds={}
 for r in annotated:
  for col in ('开始标签','结束标签'):
   term=r.get(col,'').strip()
   if re.fullmatch(r'[\w.\[\],]+\s*=\s*(?:INT#)?-?\d+',term,re.I):
    seeds.setdefault(term,{'状态种子':term,'证据POU':r.get('证据POU',''),'处理建议':'source_trace_required'})
 with (out/'state_seed_trace_queue.csv').open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=['状态种子','证据POU','处理建议']);w.writeheader();w.writerows(seeds.values())
 for name,rr in [('candidate_process_audit.csv',annotated),('process_focus_queue.csv',[r for r in annotated if r['处理建议']!='review_only'])]:
  with (out/name).open('w',newline='',encoding='utf-8-sig') as f:
   w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rr)
 result={'input_candidates':len(rows),'focus_queue':len(annotated)-counts['review_only'],'state_seeds_to_trace':len(seeds),'status_counts':dict(counts),'warning':'focus_queue是工艺审查优先队列，不等于经验证的最终真实动作，也不是对GT进行的筛选。'}
 (out/'process_focus_metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('candidates');p.add_argument('-o','--out',required=True);a=p.parse_args();print(json.dumps(classify_file(a.candidates,a.out),ensure_ascii=False,indent=2))
