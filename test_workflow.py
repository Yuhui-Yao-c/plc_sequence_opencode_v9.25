import unittest,tempfile,json,csv
from pathlib import Path
from workflow import validate,csv_write,FINAL_FIELDS
class Tests(unittest.TestCase):
 def setup_case(self,root):
  o=Path(root);(o/'batches').mkdir()
  c={'候选ID':'C0000001','开始标签':'S=10','结束标签':'S=20'}
  e={'事件ID':'E0000001','guard':'S=10'}
  csv_write(o/'indexed_candidates.csv',[c],list(c));csv_write(o/'indexed_events.csv',[e],list(e))
  (o/'workflow_manifest.json').write_text(json.dumps({'batches':[{'batch_id':'B00001','output':'batches/B00001.result.json','candidate_ids':['C0000001'],'event_ids':['E0000001']}]}))
  return o
 def test_missing_batch(self):
  with tempfile.TemporaryDirectory() as d:
   o=self.setup_case(d);self.assertFalse(validate(o)['success'])
 def test_complete_batch(self):
  with tempfile.TemporaryDirectory() as d:
   o=self.setup_case(d)
   (o/'batches/B00001.result.json').write_text(json.dumps({'batch_id':'B00001','steps':[{'开始标签':'S=10','结束标签':'S=20','证据候选ID':['C0000001'],'证据事件ID':['E0000001']}],'unresolved_events':[]}))
   self.assertTrue(validate(o,True)['success']);self.assertTrue((o/'final_sequence.csv').exists())
 def test_unaccounted_event(self):
  with tempfile.TemporaryDirectory() as d:
   o=self.setup_case(d)
   (o/'batches/B00001.result.json').write_text(json.dumps({'batch_id':'B00001','steps':[{'开始标签':'S=10','结束标签':'S=20','证据候选ID':['C0000001'],'证据事件ID':[]}],'unresolved_events':[]}))
   self.assertFalse(validate(o)['success'])
if __name__=='__main__':unittest.main()
