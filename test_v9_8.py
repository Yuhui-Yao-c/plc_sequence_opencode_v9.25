import csv,json,tempfile,unittest,hashlib,random
from pathlib import Path
from smc2_controlflow import Grid,GridError
from successor_graph import collect
from boundary_normalizer import Normalizer
from boundary_logic import parse,compatible
from workflow import pack_controller_batches


def ld(var,x,y=0,**kw):return {'__type':'LD','Var':var,'X':x,'Y':y,**kw}
def prm(arg,val):return {'__type':'PRM','Arg':arg,'Var':str(val)}
def eq(var,n,x=1,y=0):return {'__type':'F','Name':'=','X':x,'Y':y,'In':[prm('In1',var),prm('In2',n)],'Out':[{'__type':'PF','Arg':''}]}
def move(var,n,x=3,y=0):return {'__type':'F','Name':'MOVE','X':x,'Y':y,'In':[prm('In',n)],'Out':[prm('Out',var)]}
def net(nodes,comment='',vls=()):return {'CLs':nodes,'CMT':comment,'VLs':list(vls)}
def basic(var='Cell.Phase[7]',a=17,b=29):return net([ld('Run',0),eq(var,a),ld('Permit',2),move(var,b)],'等待下游允许')
def xml(nets):return '<smc2><entry type="json" name="SyntheticUnit"><![CDATA['+'\n\n'.join(json.dumps(n,ensure_ascii=False) for n in nets)+']]></entry></smc2>'
def scan(nets):
 with tempfile.TemporaryDirectory() as d:
  p=Path(d)/'src.txt';p.write_text(xml(nets),encoding='utf-8');return collect(p)

class Smc2LocalGuardTests(unittest.TestCase):
 def test_sequential_state_writes_not_claimed_as_nearest(self):
  n=net([eq('Cell.Phase[7]',17,0),move('Cell.Phase[7]',29,1),move('Cell.Phase[7]',41,2)])
  g=scan([n]);self.assertEqual([(e['from'],e['to']) for e in g['transitions']],[(17,29)])
  self.assertTrue(any('scan_order' in b['reason'] for b in g['blockers']))
 def test_two_move_paths_have_separate_provenance(self):
  n=net([eq('Cell.Phase[7]',17,0),move('Cell.Phase[7]',29,1),eq('Other.Phase[2]',5,0,1),move('Other.Phase[2]',9,1,1)])
  g=scan([n]);e=next(e for e in g['transitions'] if e['variable']=='Other.Phase[2]')
  self.assertFalse(any('@(0, 0)' in p for p in e['path']))
 def test_wait_without_actuator(self):
  g=scan([basic()]);e=g['transitions'][0]
  self.assertEqual((e['from'],e['to']),(17,29));self.assertIn('Permit',e['guard']);self.assertIn('Run',e['guard'])
  self.assertEqual(g['stage_events'][0][4],'state_wait')
 def test_direct_exit_normalizes_when_no_unknown_writer(self):
  g=scan([basic()]);r=Normalizer(g).normalize({'开始标签':'Cell.Phase[7]=17','结束标签':'NOT(Cell.Phase[7]=17)'})
  self.assertEqual(r['结束标签'],'Cell.Phase[7]=29')
 def test_lower_successor_not_numerical_sort(self):
  g=scan([basic(a=82,b=11)]);self.assertEqual(g['transitions'][0]['to'],11)
 def test_reversed_eq_and_typed_integer(self):
  n=basic();n['CLs'][1]['In']=[prm('In1','UINT#17'),prm('In2','Cell.Phase[7]')]
  n['CLs'][-1]['In']=[prm('In','DINT#29')];self.assertEqual(scan([n])['transitions'][0]['from'],17)
 def test_other_branch_not_conjoined(self):
  n=net([ld('Run',0),eq('Cell.Phase[7]',17),ld('A',2),ld('B',2,1),move('Cell.Phase[7]',29)],vls=[{'X':2},{'X':3}])
  e=scan([n])['transitions'][0];self.assertIn(' OR ',e['guard']);self.assertIn(' AND A ',e['guard']);self.assertIn(' AND B ',e['guard'])
 def test_different_successors_preserved(self):
  n=net([ld('Run',0),eq('Cell.Phase[7]',17),ld('A',2),move('Cell.Phase[7]',29),ld('A',2,1,Not=True),move('Cell.Phase[7]',41,3,1)],vls=[{'X':2}])
  self.assertEqual({r['to'] for r in scan([n])['transitions']},{29,41})
 def test_unrelated_same_network_eq_is_not_guard(self):
  n=net([eq('Cell.Phase[7]',17,0),ld('OtherPermit',0,1),move('Cell.Phase[7]',29,1,1)])
  g=scan([n]);self.assertFalse(g['transitions']);self.assertTrue(g['blockers'])
 def test_missing_cell_blocks(self):
  n=basic();n['CLs'][-1]['X']=4;g=scan([n]);self.assertFalse(g['transitions'])
 def test_duplicate_cell_blocks(self):
  n=basic();n['CLs'].append(ld('Other',2));self.assertFalse(scan([n])['transitions'])
 def test_negative_contact(self):
  n=basic();n['CLs'][2]['Not']=True;self.assertIn('NOT (Permit)',scan([n])['transitions'][0]['guard'])
 def test_opaque_fb_does_not_become_done(self):
  n=basic();n['CLs'][2]={'__type':'FB','Name':'CustomBlock','X':2}
  self.assertFalse(scan([n])['transitions'])
 def test_opaque_parallel_action_does_not_hide_feedback_branch(self):
  n=net([ld('Run',0),eq('Cell.Phase[7]',17),{'__type':'FB','Name':'CustomBlock','X':2},ld('Done',2,1),move('Cell.Phase[7]',29,3,1)],vls=[{'X':2}])
  self.assertEqual(len(scan([n])['transitions']),1)
 def test_rising_contact_not_mislabeled_as_level(self):
  n=basic();n['CLs'][2]['Up']=True;self.assertFalse(scan([n])['transitions'])
 def test_dynamic_target_blocks(self):
  n=basic();n['CLs'][-1]['In']=[prm('In','ComputedTarget')];g=scan([n]);self.assertFalse(g['transitions']);self.assertTrue(g['blockers'])
 def test_comment_number_never_creates_state(self):
  n=basic();n['CMT']='91-旧注释，等待下游';g=scan([n])
  self.assertNotIn(91,{r['value'] for r in g['state_observations']});self.assertEqual(g['comment_state_diagnostics'][0]['comment_number'],91)
 def test_unknown_reset_blocks_exit_conversion(self):
  n=net([ld('Reset',0),move('Cell.Phase[7]',0,1)])
  g=scan([basic(),n]);r=Normalizer(g).normalize({'开始标签':'Cell.Phase[7]=17','结束标签':'NOT(Cell.Phase[7]=17)'})
  self.assertTrue(r['边界规范化状态'].startswith('待核验'))
 def test_embedded_st_gate_preserved(self):
  n=net([ld('Run',0),{'__type':'IST','X':1,'TXT':'IF Cell.Phase[7] = 17 AND Permit THEN Cell.Phase[7] := 29; END_IF;'}])
  e=scan([n])['transitions'][0];self.assertEqual(e['evidence'],'smc2_embedded_st_guard');self.assertIn('Run',e['guard'])
 def test_comments_in_st_not_executable(self):
  n=net([{'__type':'IST','TXT':'(* IF Cell.Phase[7]=999 THEN Cell.Phase[7]:=1000; END_IF; *) IF Cell.Phase[7]=17 THEN Cell.Phase[7]:=29; END_IF;'}])
  g=scan([n]);self.assertNotIn(999,{r['value'] for r in g['state_observations']})
 def test_renaming_and_random_states(self):
  r=random.Random(93)
  for i in range(20):
   a,b=r.sample(range(2,20000),2);v=f'Device_{i}.Phase[{i}]';g=scan([basic(v,a,b)])
   self.assertEqual((g['transitions'][0]['variable'],g['transitions'][0]['from'],g['transitions'][0]['to']),(v,a,b))
 def test_or_of_other_controller_states_not_false_contradiction(self):
  e=parse('A.Phase=17 AND (B.Phase=2 OR B.Phase=3) AND Ready')
  self.assertTrue(compatible(e,'A.Phase',17))
  self.assertFalse(compatible(parse('A.Phase=17 AND Ready AND NOT Ready'),'A.Phase',17))

class RecallAndDiagnosisTests(unittest.TestCase):
 def test_small_two_state_machine_survives_legacy_threshold(self):
  from agent import run
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'source.txt';p.write_text(xml([basic(a=17,b=29),basic(a=29,b=17)]));o=Path(d)/'out'
   run(p,o,pattern='Cell.Phase[*]')
   rs=list(csv.DictReader((o/'prediction_candidate_pairs.csv').open(encoding='utf-8-sig')))
   self.assertTrue(any(r['开始标签']=='Cell.Phase[7]=17' and r['结束标签']=='Cell.Phase[7]=29' for r in rs))
   self.assertTrue(json.loads((o/'source_stage_preservation.json').read_text())['no_source_stage_pair_lost'])
 def test_diagnostic_does_not_inject_missing_label(self):
  from agent import run
  from diagnose_labels import diagnose
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'source.txt';p.write_text(xml([basic()]));o=Path(d)/'out';run(p,o,pattern='Cell.Phase[*]')
   before=(o/'prediction_candidate_pairs.csv').read_bytes();r=diagnose(o,'Cell.Phase[7]=999')
   self.assertIn('未见',r['诊断']);self.assertEqual(before,(o/'prediction_candidate_pairs.csv').read_bytes())
   self.assertIn('已存在',diagnose(o,'Cell.Phase[7]=17','Cell.Phase[7]=29')['诊断'])
 def test_events_packed_with_their_pair(self):
  cs=[{'候选ID':f'C{i}','开始标签':f'S={i}','结束标签':f'S={i+1}','状态':'smc2_wired_wait_transition'} for i in range(9)]
  es=[{'事件ID':f'E{i}','guard':f'S={8-i}','next_state':f'S={9-i}'} for i in range(9)]
  packs=pack_controller_batches(cs,es,2)
  for p in packs:
   for e in p['e']:self.assertTrue(any(c['开始标签']==e['guard'] and c['结束标签']==e['next_state'] for c in p['c']))
  self.assertEqual(sum(len(p['e']) for p in packs),9)
 def test_overflow_keeps_evidence_and_context_bound(self):
  cs=[{'候选ID':'C1','开始标签':'S=1','结束标签':'S=2','状态':'smc2_wired_wait_transition'}]
  es=[{'事件ID':f'E{i}','guard':'S=1','next_state':'S=2'} for i in range(8)]
  packs=pack_controller_batches(cs,es,2)
  self.assertEqual(sum(len(p['e']) for p in packs),8)
  self.assertTrue(all(len(p['e'])<=2 and p['c'][0]['候选ID']=='C1' for p in packs))
 def test_broken_json_recovers_but_blocks_proof(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'source.txt';p.write_text('<smc2><entry type="json"><![CDATA[{broken}\n'+json.dumps(basic())+']]></entry></smc2>')
   g=collect(p);self.assertEqual(g['smc2_audit']['networks'],1);self.assertTrue(g['smc2_audit']['parse_issues'])
   r=Normalizer(g).normalize({'开始标签':'Cell.Phase[7]=17','结束标签':'NOT(Cell.Phase[7]=17)'})
   self.assertTrue(r['边界规范化状态'].startswith('待核验'))
if __name__=='__main__':unittest.main()
