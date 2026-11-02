import unittest,tempfile
from pathlib import Path
from smc2_adapter import generate
class TestSmc2(unittest.TestCase):
 def test_embedded_json_state(self):
  payload='{"CLs":[{"__type":"F","Name":"=","In":[{"__type":"PRM","Arg":"In1","Var":"Phase"},{"__type":"PRM","Arg":"In2","Var":"10"}]},{"__type":"F","Name":"MOVE","In":[{"__type":"PRM","Arg":"In","Var":"20"}],"Out":[{"__type":"PRM","Arg":"Out","Var":"Phase"}]}]}'
  xml='<smc2><entry type="json"><![CDATA['+payload+'\n'+payload.replace("10","20").replace("20","30")+'\n'+payload.replace("10","30").replace("20","40")+']]></entry></smc2>'
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'sample.xml';p.write_text(xml,encoding='utf8')
   r=generate(p,Path(t)/'out');self.assertEqual(r['controller_count'],1);self.assertGreater(r['candidate_count'],2)
if __name__=='__main__':unittest.main()
