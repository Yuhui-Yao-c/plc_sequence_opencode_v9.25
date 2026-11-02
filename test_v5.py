import unittest
from benchmark_v5 import exit_equivalent,clauses,evaluate
class TestV5(unittest.TestCase):
 def test_typed_integer(self):self.assertEqual(clauses('X[2]=INT#100 OR X[2]=200'),(('X[2]',100),('X[2]',200)))
 def test_de_morgan(self):self.assertTrue(exit_equivalent('X=100 OR X=200','NOT(X=100) AND NOT(X=200)',{'X=100','X=200'}))
 def test_no_wrong_or(self):self.assertFalse(exit_equivalent('X=100 OR X=200','NOT(X=100)',{'X=100','X=200'}))
 def test_cross_controller(self):self.assertIsNone(clauses('X=100 OR Y=200'))
 def test_no_fake_path(self):
  res=evaluate([{'开始标签':'X=100','结束标签':'X=200','状态':'adjacency_hypothesis'}],[('X=100','X=200','demo')]);self.assertFalse(res['records'][0]['source_network_path'])
if __name__=='__main__':unittest.main()
