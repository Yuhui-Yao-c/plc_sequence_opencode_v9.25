import unittest,tempfile
from pathlib import Path
from plcopen_action_trace import scan

XML='''<project><pous><pou name="Unit"><body><LD>
<inVariable localId="1"><expression>Step</expression></inVariable>
<inVariable localId="2"><expression>170</expression></inVariable>
<block localId="10" typeName="EQ"><inputVariables><variable formalParameter="IN1"><connectionPointIn><connection refLocalId="1"/></connectionPointIn></variable><variable formalParameter="IN2"><connectionPointIn><connection refLocalId="2"/></connectionPointIn></variable></inputVariables></block>
<contact localId="11"><variable>PositionOK</variable><connectionPointIn><connection refLocalId="10"/></connectionPointIn></contact>
<coil localId="12"><variable>AxisMoveExecute</variable><connectionPointIn><connection refLocalId="11"/></connectionPointIn></coil>
<inVariable localId="13"><expression>195</expression></inVariable>
<block localId="14" typeName="MOVE"><inputVariables><variable formalParameter="EN"><connectionPointIn><connection refLocalId="11"/></connectionPointIn></variable><variable formalParameter="IN"><connectionPointIn><connection refLocalId="13"/></connectionPointIn></variable></inputVariables><outputVariables><variable formalParameter="OUT"><expression>Step</expression></variable></outputVariables></block>
<coil localId="99"><variable>UnconnectedMotor</variable></coil>
</LD></body></pou></pous></project>'''
class TraceTests(unittest.TestCase):
 def test_reachable_action_transition_not_unconnected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'src.xml';p.write_text(XML)
   rows,meta=scan(p)
   self.assertEqual(len(rows),2)
   self.assertEqual({r['target_id'] for r in rows},{'12','14'})
   self.assertEqual([r for r in rows if r['target_id']=='14'][0]['next_state'],'Step=195')
 def test_no_action_when_only_eq(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'src.xml';p.write_text(XML.replace('refLocalId="11"','refLocalId="999"'))
   rows,_=scan(p)
   self.assertEqual(rows,[])
if __name__=='__main__':unittest.main()
