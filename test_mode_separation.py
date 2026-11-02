import unittest, tempfile, csv, json
from pathlib import Path
import plc_cli

class SeparationTests(unittest.TestCase):
 def test_predict_rejects_gt_argument(self):
  from unittest.mock import patch
  with patch('sys.argv',['plc_cli.py','predict','--source','x.xml','--out','a','--gt','truth.tsv']):
   with self.assertRaises(SystemExit):plc_cli.main()
 def test_evaluate_does_not_modify_prediction(self):
  with tempfile.TemporaryDirectory() as td:
   td=Path(td);p=td/'pred.csv';g=td/'gt.tsv';o=td/'eval'
   with p.open('w',encoding='utf-8',newline='') as f:
    w=csv.writer(f);w.writerow(['开始标签','结束标签','状态']);w.writerow(['Step=10','Step=20','wired_guard_local'])
   g.write_text('动作\t开始\t结束\n测试\tStep=10\tStep=20\n',encoding='utf-8')
   before=plc_cli.fingerprint(p)
   a=type('Args',(),{'prediction':str(p),'gt':str(g),'out':str(o),'relations':None,'top_k':3,'no_excel':True})()
   plc_cli.evaluate(a)
   self.assertEqual(before,plc_cli.fingerprint(p));self.assertTrue((o/'gt_match_hits.csv').exists())
   self.assertEqual(json.loads((o/'evaluation_provenance.json').read_text())['gt_used_only_for_evaluation'],True)
 def test_evaluate_rejects_gt_as_relations(self):
  with tempfile.TemporaryDirectory() as td:
   td=Path(td);p=td/'prediction.csv';g=td/'gt.tsv';p.write_text('开始标签,结束标签\nA=1,A=2\n');g.write_text('动作\t开始\t结束\n测试\tA=1\tA=2\n')
   a=type('Args',(),{'prediction':str(p),'gt':str(g),'out':str(td/'result'),'relations':str(g),'top_k':3,'no_excel':True})()
   with self.assertRaises(ValueError):plc_cli.evaluate(a)
if __name__=='__main__':unittest.main()
