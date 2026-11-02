import unittest
from gt_search import search_one
class Tests(unittest.TestCase):
 def test_exact(self):
  c=[{'开始标签':'ST17.AutoStep[1]=100','结束标签':'ST17.AutoStep[1]=100 AND SV78.PosOK[2]'}]
  self.assertEqual(search_one('ST17.AutoStep[1]=100','ST17.AutoStep[1]=100 AND SV78.PosOK[2]',c)[0],'严格起止命中')
 def test_no_false_equivalence(self):
  c=[{'开始标签':'ST17.AutoStep[1]=100','结束标签':'ST17.AutoStep[1]=110'}]
  self.assertEqual(search_one('ST17.AutoStep[1]=100','ST17.AutoStep[1]=100 AND SV78.PosOK[2]',c)[0],'同起点/终点不同')
 def test_conditional(self):
  c=[{'开始标签':'ST17.AutoStep[1]=100','结束标签':'ST17.AutoStep[1]=110'}]
  rel=[dict(src='st17.autostep[1]=100',dst='st17.autostep[1]=110',guard='sv78.posok[2]',proof='wired_guard_local')]
  self.assertEqual(search_one('ST17.AutoStep[1]=100','ST17.AutoStep[1]=100 AND SV78.PosOK[2]',c,rel)[0],'有条件的迁移替代')
 def test_prefix(self):
  c=[{'开始标签':'m_nNextAutoStep[3]=110','结束标签':'NOT(m_nNextAutoStep[3]=110)'}]
  self.assertEqual(search_one('m_nNextAutoStep[3]=INT#110','NOT(m_nNextAutoStep[3]=INT#110)',c)[0],'严格起止命中')
if __name__=='__main__':unittest.main()
