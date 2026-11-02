import unittest
from benchmark_v7_1 import evaluate

class Regression(unittest.TestCase):
 def test_feedback_not_equivalent_to_exit(self):
  candidates=[{'开始标签':'ST09.AutoStep[1]=20','结束标签':'NOT (ST09.AutoStep[1]=20)','状态':'state_exit'}]
  r=evaluate(candidates,[('ST09.AutoStep[1]=20','ST09.AutoStep[1]=20 AND SV42.PosOK[1]','轴到位')])
  self.assertEqual(r['records'][0]['对比结果'],'起点已定位/反馈终点缺失')
 def test_precise_feedback_has_priority(self):
  c=[{'开始标签':'ST01.AutoStep[3]=130','结束标签':'ST01.AutoStep[3]=130 AND SV17.PosOK[1]','状态':'feedback'}]
  r=evaluate(c,[('ST01.AutoStep[3]=130','ST01.AutoStep[3]=130 AND SV17.PosOK[1]','轴')])
  self.assertEqual(r['records'][0]['对比结果'],'直接匹配')
 def test_state_exit_stays_separate(self):
  c=[{'开始标签':'X=2','结束标签':'NOT(X=2)','状态':'state_exit'}]
  r=evaluate(c,[('X=2','NOT(X=2)','state exit')])
  self.assertEqual(r['records'][0]['对比结果'],'直接匹配')
if __name__=='__main__':unittest.main()
