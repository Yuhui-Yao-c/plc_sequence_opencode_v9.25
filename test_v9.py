import tempfile, unittest
from pathlib import Path
from plcopen_controlflow import scan

EXAMPLE='''<project><types/><pous><pou name="Demo"><body><LD>
<block localId="10" typeName="EQ"><inputVariables><variable formalParameter="IN1"><connectionPointIn><connection refLocalId="1"/></connectionPointIn></variable><variable formalParameter="IN2"><connectionPointIn><connection refLocalId="2"/></connectionPointIn></variable></inputVariables></block>
<inVariable localId="1"><expression>Step</expression></inVariable><inVariable localId="2"><expression>100</expression></inVariable>
<contact localId="12"><variable>Done</variable><connectionPointIn><connection refLocalId="10"/></connectionPointIn></contact>
<block localId="20" typeName="MOVE"><inputVariables><variable formalParameter="EN"><connectionPointIn><connection refLocalId="10"/></connectionPointIn></variable><variable formalParameter="IN"><connectionPointIn><connection refLocalId="21"/></connectionPointIn></variable></inputVariables><outputVariables><variable formalParameter="OUT"><connectionPointOut/><expression>Step</expression></variable></outputVariables></block>
<inVariable localId="21"><expression>110</expression></inVariable>
</LD></body></pou></pous></project>'''
class V9Tests(unittest.TestCase):
 def test_wired_equal(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'demo.xml';p.write_text(EXAMPLE)
   rows,meta=scan(p)
   self.assertEqual(len(rows),1)
   self.assertEqual(rows[0]['source_state'],100)
   self.assertEqual(rows[0]['target_state'],110)
   self.assertEqual(rows[0]['proof_level'],'wired_guard_local')
 def test_missing_guard_not_proven(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'demo.xml';p.write_text(EXAMPLE.replace('refLocalId="10"/></connectionPointIn></variable><variable formalParameter="IN"','refLocalId="999"/></connectionPointIn></variable><variable formalParameter="IN"'))
   rows,_=scan(p)
   self.assertEqual(rows[0]['proof_level'],'wired_guard_incomplete')
   self.assertEqual(rows[0]['source_state'],'')
if __name__=='__main__':unittest.main()
