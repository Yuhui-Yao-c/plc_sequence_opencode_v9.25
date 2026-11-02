import unittest
from candidate_filter import match_boundary,extract_symbols
from timing_equivalence import analyze_transition
class FilterTests(unittest.TestCase):
 def test_glob(self):self.assertTrue(match_boundary('ST09.AutoStep[1]=20','SV42.PosOK[1]','ST*.AutoStep[*]'))
 def test_either(self):self.assertTrue(match_boundary('other=1','NOT (ST17.AutoStep[1]=110)','ST*.AutoStep[*]'))
 def test_exclude_unrelated(self):self.assertFalse(match_boundary('AxisValP[10].HMI_CurMotorLoc=2','other=1','ST*.AutoStep[*]'))
 def test_literal_brackets(self):self.assertTrue(match_boundary('ST01.AutoStep[2]=100','','ST01.AutoStep[2]'))
 def test_no_substring(self):self.assertFalse(match_boundary('OTHER_ST01.AutoStep[2]=100','','ST*.AutoStep[*]'))
 def test_prefixed_numeric(self):self.assertIn('m_nNextAutoStep[3]',extract_symbols('m_nNextAutoStep[3]=INT#110'))
 def test_case(self):self.assertTrue(match_boundary('st17.autostep[1]=100','','ST*.AutoStep[*]'))
 def test_polarity(self):self.assertTrue(match_boundary('NOT ST02.AutoStep[1]=2','','ST*.AutoStep[*]'))
 def test_feedback_not_equivalence(self):
  r=analyze_transition('ST17.AutoStep[1]=100','SV78.PosOK[2]','ST17.AutoStep[1]=110')
  self.assertFalse(r['strictly_equivalent']);self.assertEqual(r['classification'],'not_proven')
 def test_conditional(self):
  r=analyze_transition('ST17.AutoStep[1]=100','SV78.PosOK[2]','ST17.AutoStep[1]=110',['run'],['run'])
  self.assertEqual(r['classification'],'conditional_same_scan_transition');self.assertFalse(r['strictly_equivalent'])
if __name__=='__main__':unittest.main()
