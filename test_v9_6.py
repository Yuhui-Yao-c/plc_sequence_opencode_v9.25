import tempfile,unittest,csv,json
from pathlib import Path
from workflow import create,csv_read,validate,csv_write,CANDIDATE_FIELDS
from gt_search import load_relations
class RegressionV96(unittest.TestCase):
 def test_pre_llm_pairs_preserve_weak_candidates(self):
  import unittest.mock
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'source.xml';src.write_text('<project/>')
   out=Path(d)/'result'
   weak={'开始标签':'Step=100','结束标签':'Step=110','状态':'adjacency_hypothesis','动作':'未确认阶段','控制器':'Step'}
   act={'开始标签':'Step=110','结束标签':'Step=120','状态':'wired_move_guard_local','动作':'执行动作','控制器':'Step'}
   def fake_extractor(source,path):
    csv_write(Path(path)/'sequence_candidates.csv',[weak,act],CANDIDATE_FIELDS)
    csv_write(Path(path)/'event_graph.csv',[],['source_format','source_unit','network','guard','command','arguments','feedback','next_state','relation','evidence','comment'])
    return {'format':'plcopen'}
   with unittest.mock.patch('workflow.extract',fake_extractor):
    create(src,out,pattern='Step')
   self.assertEqual(len(csv_read(out/'prediction_candidate_pairs.csv')),2)
   self.assertEqual(len(csv_read(out/'sequence_candidates_filtered.csv')),2)
   self.assertTrue((out/'process_focus_queue.csv').exists())
 def test_incomplete_relation_not_accepted(self):
  with tempfile.TemporaryDirectory() as d:
   f=Path(d)/'r.csv';f.write_text('state_variable,source_state,target_state,feedback,other_guards,proof_level\nStep,100,110,PosOK,,wired_guard_incomplete\n',encoding='utf-8')
   self.assertEqual(load_relations(f)[0]['proof'],'wired_guard_incomplete')
if __name__=='__main__':unittest.main()
