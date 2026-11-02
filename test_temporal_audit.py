import unittest
from temporal_audit import atoms,diagnose,timing_relations,canon
class TestTemporalAudit(unittest.TestCase):
 def test_typed_number(self):self.assertIn(('a.step[1]',110),atoms('A.Step[1] = INT#110'))
 def test_strict_canon(self):self.assertEqual(canon('X = INT#100'),canon('X=100'))
 def test_no_false_gold_error(self):
  d=diagnose([{'人工开始标签':'ST01.AutoStep[1]=20','人工结束标签':'ST01.AutoStep[1]=20 AND SV42.PosOK[1]'}],[{'开始标签':'ST01.AutoStep[1]=20','结束标签':'ST01.AutoStep[1]=30'}]);self.assertEqual(d['rows'][0]['疑似人工错误'],'未证实')
 def test_timing_not_verified(self):
  a=timing_relations([{'guard':'Step=100','next_state':'Step=110','feedback':'Axis.PosOK','source_format':'smc2'}]);self.assertEqual(len(a),1);self.assertEqual(a[0]['时间戳相等'],'未验证')
 def test_different_controller(self):self.assertEqual(timing_relations([{'guard':'StepA=100','next_state':'StepB=110','feedback':'A.Done'}]),[])
if __name__=='__main__':unittest.main()
