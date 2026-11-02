"""Conservative timing-boundary relations. Equality of booleans is NOT equality of sampling timestamps."""
import csv,re,json,argparse
from pathlib import Path
ATOM=re.compile(r'(?P<var>[\w.\[\]]+)\s*=\s*(?:INT#)?(?P<value>-?\d+)',re.I)
def atom(expr):
 m=ATOM.fullmatch((expr or '').strip());return (m.group('var'),int(m.group('value'))) if m else None
def analyze_transition(current,feedback,next_state,guard_conditions=None,assignment_conditions=None):
 """Report a conditional relationship; never assert unconditional timestamp equivalence.

 A feedback-enabled MOVE executes when its complete rung is true. If enabled in same scan,
 the *next state* is visible after execution. Tag collectors can sample before/after writes.
 """
 a=atom(current);b=atom(next_state)
 reasons=[]
 if not a or not b or a[0]!=b[0] or a[1]==b[1]:reasons.append('不是同一状态变量的明确状态转换')
 if not feedback or not re.match(r'^[\w.\[\]]+$',feedback):reasons.append('反馈不是单一可识别的布尔信号')
 if guard_conditions is None:reasons.append('缺少可验证的完整使能条件')
 if assignment_conditions is None:reasons.append('缺少MOVE完整使能链')
 if guard_conditions is not None and assignment_conditions is not None and set(guard_conditions)!=set(assignment_conditions):
  reasons.append('反馈事件与状态赋值的门控条件不同')
 return {'feedback_boundary':current+' AND '+feedback if feedback else '', 'state_boundary':next_state,
  'classification':'not_proven' if reasons else 'conditional_same_scan_transition',
  'strictly_equivalent':False,
  'conditions':guard_conditions or [],'reasons':reasons,
  'timing_note':'只有在完整门控成立且MOVE执行后，后继状态才能成立。PLC扫描时序、采样位置、异步采集和中间其他赋值可造成时间差；不能默认时间戳严格相等。'}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--current',required=True);p.add_argument('--feedback',required=True);p.add_argument('--next',required=True);a=p.parse_args()
 print(json.dumps(analyze_transition(a.current,a.feedback,a.next),ensure_ascii=False,indent=2))
