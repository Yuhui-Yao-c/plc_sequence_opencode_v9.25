import tempfile,unittest,csv
from pathlib import Path
from agent import run
from benchmark_v3 import assess
XML='''<?xml version="1.0"?><project xmlns="http://www.plcopen.org/xml/tc6_0200"><types><pous><pou name="P"><body><ST><xhtml xmlns="http://www.w3.org/1999/xhtml">IF Phase=10 THEN Phase:=20; END_IF; IF Phase=20 THEN Phase:=30; END_IF; IF Phase=30 THEN Phase:=40; END_IF;</xhtml></ST></body></pou></pous></types></project>'''
class TestRecall(unittest.TestCase):
 def test_small_xml(self):
  with tempfile.TemporaryDirectory() as t:
   inp=Path(t)/'test.xml';inp.write_text(XML,encoding='utf8')
   result=run(inp,Path(t)/'out');self.assertGreaterEqual(result['candidate_count'],2)
   with (Path(t)/'out'/'sequence_candidates.csv').open(encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
   self.assertIn('adjacency_hypothesis',{r['状态'] for r in rows})
 def test_benchmark_does_not_claim_coverage(self):
  result=assess([{'开始标签':'Phase=10','结束标签':'Phase=20'}],[('Phase=10','Phase=30','test')]);self.assertEqual(result['metrics']['exact'],0)
if __name__=='__main__':unittest.main()
