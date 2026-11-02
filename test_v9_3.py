import unittest
from gt_search import evaluate

class OneToOneTests(unittest.TestCase):
 def test_duplicate_gt_single_pair(self):
  c=[{'开始标签':'Step=100','结束标签':'Step=110'}]
  gt=[('Step=100','Step=110','a'),('Step=100','Step=110','b')]
  rows,hits,count=evaluate(c,gt)
  self.assertEqual(sum(x['计入覆盖']=='是' for x in rows),1)
  self.assertEqual(count['匹配冲突GT数'],1)
  self.assertEqual(len(set(x['唯一匹配候选行号'] for x in rows if x['计入覆盖']=='是')),1)
 def test_duplicate_prediction_pair_capacity_one(self):
  c=[{'开始标签':'Step=100','结束标签':'Step=110'}]*2
  gt=[('Step=100','Step=110','a'),('Step=100','Step=110','b')]
  rows,_,count=evaluate(c,gt)
  self.assertEqual(count['唯一计分GT数'],1)
 def test_distinct_pairs_can_match(self):
  c=[{'开始标签':'Step=100','结束标签':'Step=110'}, {'开始标签':'Step=100','结束标签':'Step=120'}]
  gt=[('Step=100','Step=110','a'),('Step=100','Step=120','b')]
  _,_,count=evaluate(c,gt)
  self.assertEqual(count['唯一计分GT数'],2)
 def test_search_overlap_not_credit(self):
  c=[{'开始标签':'Step=100','结束标签':'Step=120'}]
  gt=[('Step=100','Step=110','a')]
  rows,_,count=evaluate(c,gt)
  self.assertEqual(rows[0]['计入覆盖'],'否')
  self.assertEqual(count['唯一计分GT数'],0)
if __name__=='__main__':unittest.main()
