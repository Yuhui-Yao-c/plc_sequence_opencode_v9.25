import unittest,tempfile
from pathlib import Path
from event_graph_v6 import plcopen_events
from benchmark_v6 import evaluate
class TestV6(unittest.TestCase):
 def test_plcopen_namespaced_event(self):
  xml='''<project xmlns="http://www.plcopen.org/xml/tc6_0200"><pou name="Example"><body><LD><contact localId="1"><variable>Axis.PosDone</variable></contact><block localId="2" typeName="AxisMove"><inputVariables><variable formalParameter="Axis"><expression>Axis1</expression></variable></inputVariables></block></LD></body></pou></project>'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'in.xml';p.write_text(xml);rows=list(plcopen_events(p))
   self.assertEqual(len(rows),1);self.assertEqual(rows[0][4],'AxisMove');self.assertIn('PosDone',rows[0][6])
 def test_no_false_match_by_start(self):
  r=evaluate([{'开始标签':'Step=1','结束标签':'Step=2','状态':'adjacency_hypothesis'}],[('Step=1','Step=9','action')])
  self.assertEqual(r['records'][0]['对比结果'],'周期待确认')
if __name__=='__main__':unittest.main()
