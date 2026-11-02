"""Synthetic regression for signal/data tails. No production identifiers/constants."""
import csv, json, random, tempfile, unittest
from pathlib import Path
from lxml import etree as E
from test_v9_9 import (node,cpin,cpout,contact,source,block,sink,basic,document,scan,X)
from boundary_logic import BoundarySyntaxError,parse
from source_expressions import SourceConditions,valid_scalar
from signal_tail_merge import discover,export_collection
from atomic_boundaries import export_atomic
from boundary_normalizer import Normalizer
from agent import run

def action(var='Cell.Phase[8]',a=17,b=23,order=1):
 r=basic(var,str(a),str(b),order)
 next(n for n in r if n.get(X)=='Comment').find('Content').text='lift motion'
 c=node(r,'LdObject','Coil',operand='Drive.JogCW',latch='set');cpin(c,5);cpout(c,20)
 return r

def tail(var='Cell.Phase[8]',a=23,b=41,order=2,code=None,timer=True):
 r=E.Element('Rung',evaluationOrder=str(order));c=node(r,'CommonObject','Comment');E.SubElement(c,'Content').text='copy compensated position'
 cpout(node(r,'LdObject','LeftPowerRail'),1);contact(r,'Run',1,2)
 source(r,var,3);source(r,str(a),4);block(r,'=',{'EN':2,'In1':3,'In2':4},{'':5})
 contact(r,'Drive.Busy',5,6,negated='true')
 if timer:
  source(r,'T#12MS',7);block(r,'TON',{'In':6,'PT':7},{'Q':8},instanceName='Settle');enabled=8
 else:enabled=6
 st=node(r,'LdObject','smcext:InlineST');cpin(st,enabled);cpout(st,9)
 E.SubElement(st,'ST').text=code or f'Param.Target := Param.Position + Compensation; IF Param.Target > Param.Limit THEN Param.Target := Param.Limit; END_IF; {var} := INT#{b};'
 return r

class SourceExpressionTests(unittest.TestCase):
 def test_numeric_predicate_preserves_text(self):
  p=SourceConditions();e=p.parse('Value.Current > Value.Limit');self.assertIn('Value.Current > Value.Limit',p.expand(e.text()))
 def test_gt_grammar_not_relaxed(self):
  with self.assertRaises(BoundarySyntaxError):parse('S=3 OR 4')
 def test_unknown_call_not_scalar(self):self.assertFalse(valid_scalar('Danger(Other)'))
 def test_missing_operator_not_repaired(self):self.assertFalse(valid_scalar('A B'))
 def test_arithmetic_supported(self):self.assertTrue(valid_scalar('A.Value + Offset * (2 - Gain)'))
 def test_boolean_mix_requires_review(self):
  with self.assertRaises(BoundarySyntaxError):SourceConditions().parse('A > B AND C')

class SignalTailTests(unittest.TestCase):
 def test_inline_timer_literal_target_recovered(self):
  g=scan([action(),tail()]);e=[e for e in g['transitions'] if e['from']==23][0]
  self.assertEqual(e['to'],41);self.assertIn('Settle.Q',e['guard']);self.assertFalse(e['guard_complete'])
 def test_timer_not_promoted_to_equivalence(self):
  g=scan([action(),tail()]);res=Normalizer(g).normalize({'开始标签':'Cell.Phase[8]=23','结束标签':'NOT(Cell.Phase[8]=23)'})
  self.assertEqual(res['结束标签'],'NOT(Cell.Phase[8]=23)')
 def test_data_effects_and_delay_recorded(self):
  g=scan([action(),tail()]);s=g['native_inline_slices'][0];self.assertEqual(len(s['assignments']),3);self.assertEqual(s['temporal'][0]['preset'],'T#12MS')
 def test_compose_after_motion_and_stop_before_next_motion(self):
  r=discover(scan([action(),tail(),action(a=41,b=80,order=3)]));self.assertEqual(len(r['proposals']),1)
  p=r['proposals'][0];self.assertEqual((p['action_start'],p['end_state']),(17,41));self.assertEqual(p['tail_states'],[23])
 def test_no_action_no_merge(self):self.assertFalse(discover(scan([basic('Cell.Phase[8]','17','23'),tail()]))['proposals'])
 def test_new_action_inside_tail_not_absorbed(self):
  t=tail();c=node(t,'LdObject','Coil',operand='Other.JogCW',latch='set');cpin(c,5);cpout(c,30)
  self.assertFalse(discover(scan([action(),t]))['proposals'])
 def test_ambiguous_predecessor_not_chosen(self):
  self.assertFalse(discover(scan([action(),action(a=7,b=23,order=4),tail()]))['proposals'])
 def test_no_state_number_adjacency(self):
  r=discover(scan([action(a=900,b=50),tail(a=50,b=2)]));self.assertEqual(r['proposals'][0]['row']['结束标签'],'Cell.Phase[8]=2')
 def test_control_assignment_not_signal_data(self):
  r=discover(scan([action(),tail(code='Other.Execute:=TRUE; Cell.Phase[8]:=41;')]))
  self.assertFalse(r['proposals'])
 def test_branching_tail_not_silently_flattened(self):
  code='Param.Value:=Param.Actual; IF Ready THEN Cell.Phase[8]:=41; ELSE Cell.Phase[8]:=42; END_IF;'
  self.assertFalse(discover(scan([action(),tail(code=code)]))['proposals'])
 def test_only_move_state_not_signal_effect(self):
  self.assertFalse(discover(scan([action(),tail(code='Cell.Phase[8]:=41;')]))['proposals'])
 def test_opaque_rhs_blocks_recovery(self):
  g=scan([tail(code='Param.Value:=UnknownCall(); Cell.Phase[8]:=41;')]);self.assertFalse(g['transitions']);self.assertTrue(g['blockers'])
 def test_return_blocks_linear_recovery(self):
  g=scan([tail(code='Param.Value:=1; RETURN; Cell.Phase[8]:=41;')]);self.assertFalse(g['transitions'])
 def test_cycle_not_composed(self):self.assertFalse(discover(scan([action(),tail(b=17)]))['proposals'])
 def test_independent_wait_requires_review(self):
  t=tail();next(n for n in t if n.get(X)=='Comment').find('Content').text='等待远端新的加工任务'
  self.assertFalse(discover(scan([action(),t]))['proposals'])
 def test_chain_two_signal_states(self):
  r=discover(scan([action(),tail(b=41),tail(a=41,b=83,order=3)]));self.assertEqual(r['proposals'][0]['tail_states'],[23,41]);self.assertEqual(r['proposals'][0]['end_state'],83)
 def test_names_and_states_randomized(self):
  rng=random.Random(811)
  for i in range(7):
   a,b,c=rng.sample(range(1,50000),3);v=f'Node{i}.Stage[{i}]'
   ps=discover(scan([action(v,a,b),tail(v,b,c)]))['proposals'];self.assertEqual((ps[0]['action_start'],ps[0]['end_state']),(a,c))
 def test_scope_isolation(self):
  root=E.fromstring(document([action()],program='A'));b=E.fromstring(document([tail()],program='B'))
  root.find('{*}Types/{*}GlobalNamespace').append(b.find('{*}Types/{*}GlobalNamespace/{*}Program'))
  from omron_native import collect_native
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'source.xml';p.write_bytes(E.tostring(root));self.assertFalse(discover(collect_native(p))['proposals'])

class PipelineMergeTests(unittest.TestCase):
 def test_raw_retained_collection_composed(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);p=d/'source.xml';p.write_bytes(document([action(),tail(),action(a=41,b=80,order=3)]));out=d/'out'
   run(p,out,'Cell.Phase[*]')
   def pairs(name):
    return {(r['开始标签'],r['结束标签']) for r in csv.DictReader((out/name).open(encoding='utf-8-sig'))}
   raw=pairs('prediction_atomic_pairs.csv');final=pairs('prediction_collection_pairs.csv')
   self.assertIn(('Cell.Phase[8]=23','Cell.Phase[8]=41'),raw)
   self.assertIn(('Cell.Phase[8]=17','Cell.Phase[8]=41'),final)
   self.assertNotIn(('Cell.Phase[8]=17','Cell.Phase[8]=23'),final)
   self.assertNotIn(('Cell.Phase[8]=23','Cell.Phase[8]=41'),final)
   self.assertIn(('Cell.Phase[8]=41','Cell.Phase[8]=80'),final)
 def test_empty_name_filter_does_not_reintroduce_merge(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);p=d/'source.xml';p.write_bytes(document([action(),tail()]));out=d/'out';run(p,out,'Missing[*]')
   self.assertEqual(list(csv.DictReader((out/'prediction_collection_pairs.csv').open(encoding='utf-8-sig'))),[])
 def test_gt_neighbor_not_used(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);p=d/'source.xml';p.write_bytes(document([action(),tail()]));(d/'gt.csv').write_text('开始标签,结束标签\nFake.Step=998,Fake.Step=999')
   out=d/'out';run(p,out,'Cell.Phase[*]');self.assertNotIn('Fake.Step',(out/'prediction_collection_pairs.csv').read_text())
 def test_no_existing_pairs_no_collection_injection(self):
  with tempfile.TemporaryDirectory() as d:
   g=scan([action(),tail()]);export_atomic([],g,d);r=export_collection(d,g);self.assertEqual(r['collection_pairs'],0)
 def test_read_only_evaluation_does_not_merge_prediction(self):
  from plc_cli import evaluate
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);p=d/'source.xml';p.write_bytes(document([action(),tail()]));pred=d/'pred.csv';gt=d/'gt.csv'
   pred.write_text('原子对ID,开始标签,结束标签\nA1,Cell.Phase[8]=17,Cell.Phase[8]=23\nA2,Cell.Phase[8]=23,Cell.Phase[8]=41')
   gt.write_text('GT开始,GT结束\nCell.Phase[8]=17,Cell.Phase[8]=41');before=pred.read_bytes()
   r=evaluate(SimpleNamespace(prediction=str(pred),gt=str(gt),xml=str(p),transition_graph=None,relations=None,out=str(d/'eval'),top_k=5,no_excel=True))
   self.assertEqual(pred.read_bytes(),before)
   # The merged pair must already exist in the prediction to score; evaluator cannot create it.
   m=json.load((d/'eval/gt_match_metrics.json').open());self.assertEqual(m.get('unique_scored_gt',m.get('strict_pair_matches',0)),0)


class MoreMergeRegressionTests(unittest.TestCase):
 def test_another_controller_write_is_not_pure_data(self):
  r=discover(scan([action(),tail(code='Other.State[1]:=7; Cell.Phase[8]:=41;'),basic('Other.State[1]','7','8',3)]))
  self.assertFalse(r['proposals'])
 def test_semantic_view_uses_source_tail_even_if_not_selected_as_action(self):
  g=scan([action(),tail()])
  with tempfile.TemporaryDirectory() as d:
   rows=[{'开始标签':'Cell.Phase[8]=17','结束标签':'Cell.Phase[8]=23','动作':'升降运动','构成要素':'Lift','统计对象':'Lift-A'}]
   export_atomic(rows,g,d,prefix='process');r=export_collection(d,g,prefix='process')
   out=list(csv.DictReader((Path(d)/'process_collection_pairs.csv').open(encoding='utf-8-sig')))
   self.assertEqual(out[0]['结束标签'],'Cell.Phase[8]=41');self.assertEqual(out[0]['动作'],'升降运动')
 def test_different_semantic_rows_not_lost(self):
  g=scan([action(),tail()])
  with tempfile.TemporaryDirectory() as d:
   rows=[{'开始标签':'Cell.Phase[8]=17','结束标签':'Cell.Phase[8]=23','动作':'move '+x,'统计对象':x} for x in ['A','B']]
   export_atomic(rows,g,d,prefix='sequence');export_collection(d,g,prefix='sequence')
   out=list(csv.DictReader((Path(d)/'sequence_collection_pairs.csv').open(encoding='utf-8-sig')))
   self.assertEqual(len(out),2);self.assertEqual({r['统计对象'] for r in out},{'A','B'})
 def test_hop_limit_does_not_truncate_then_claim_merged(self):
  g=scan([action(),tail(b=41),tail(a=41,b=83,order=3)])
  self.assertFalse(discover(g,max_hops=1)['proposals'])
 def test_prepare_and_finish_python_workflow_contains_merge(self):
  from workflow import create,validate
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);src=d/'src.xml';src.write_bytes(document([action(),tail()]));out=d/'run'
   m=create(src,out,pattern='Cell.Phase[*]')
   for b in m['batches']:
    inp=json.load((out/b['input']).open());steps=[]
    for c in inp['candidates']:
     if c.get('状态')=='action_signal_tail_composed':
      steps.append({'构成要素':'测试升降','动作':'移动并传递参数','动作描述':'测试合并',
        '开始标签':c['开始标签'],'结束标签':c['结束标签'],'核验状态':'candidate',
        '证据候选ID':[c['候选ID']],'证据事件ID':[],'说明':'测试，不是LLM实跑',
        '过程类型':'机械动作','统计对象':'测试机构','起点含义':'动作开始','终点含义':'信号处理结束',
        '工艺依据':'源图','边界依据':'源图'})
    result={'batch_id':b['batch_id'],'steps':steps,'unresolved_events':[{'event_id':i,'reason':'测试'} for i in b['event_ids']]}
    (out/b['output']).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
   result=validate(out,True);self.assertTrue(result['success'])
   pp=list(csv.DictReader((out/'final_prediction_pairs.csv').open(encoding='utf-8-sig')))
   self.assertIn(('Cell.Phase[8]=17','Cell.Phase[8]=41'),{(x['开始标签'],x['结束标签']) for x in pp})

if __name__=='__main__':unittest.main()
