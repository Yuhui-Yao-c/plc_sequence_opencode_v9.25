"""Native port-based export regression tests. Synthetic names/states only."""
import csv,hashlib,json,random,tempfile,unittest
from pathlib import Path
from lxml import etree as E
from source_format import detect_source_format,IEC10_NS
from omron_native import NativeRung,collect_native
from successor_graph import collect
from boundary_normalizer import Normalizer

X='{http://www.w3.org/2001/XMLSchema-instance}type'

def node(r,tag,typ,**attrs):return E.SubElement(r,tag,{X:typ,**{k:str(v) for k,v in attrs.items()}})
def cpout(n,num):return E.SubElement(n,'ConnectionPointOut',connectionPointOutId=str(num))
def cpin(n,*ids):
 p=E.SubElement(n,'ConnectionPointIn')
 for i in ids:E.SubElement(p,'Connection',refConnectionPointOutId=str(i))
 return p
def source(r,value,out):
 n=node(r,'FbdObject','DataSource',identifier=value);cpout(n,out);return n
def block(r,name,ins,outs,**attrs):
 b=node(r,'FbdObject','Block',typeName=name,**attrs)
 ip=E.SubElement(b,'InputVariables')
 for arg,ids in ins.items():
  v=E.SubElement(ip,'InputVariable',parameterName=arg);cpin(v,*(ids if isinstance(ids,list) else [ids]))
 op=E.SubElement(b,'OutputVariables')
 for arg,ref in outs.items():cpout(E.SubElement(op,'OutputVariable',parameterName=arg),ref)
 return b
def sink(r,var,ref):
 n=node(r,'FbdObject','DataSink',identifier=var);cpin(n,ref);return n
def contact(r,var,ref,out,**attrs):
 n=node(r,'LdObject','Contact',operand=var,**attrs);cpin(n,*([ref] if not isinstance(ref,list) else ref));cpout(n,out);return n
def basic(var='Cell.Phase[7]',a='INT#17',b='29',order=1):
 r=E.Element('Rung',evaluationOrder=str(order))
 c=node(r,'CommonObject','Comment');E.SubElement(c,'Content').text='等待外部许可'
 cpout(node(r,'LdObject','LeftPowerRail'),1);contact(r,'Run',1,2)
 source(r,var,3);source(r,a,4);block(r,'=',{'EN':2,'In1':3,'In2':4},{'':5})
 contact(r,'Permit',5,6);source(r,b,7);block(r,'MOVE',{'EN':6,'In':7},{'ENO':8,'Out':9});sink(r,var,9)
 return r

def document(rungs,locals_=(),prefix=False,program='Unit'):
 root=E.Element('Project',nsmap={None:IEC10_NS,'xsi':'http://www.w3.org/2001/XMLSchema-instance','smcext':'https://www.ia.omron.com/Smc'})
 ts=E.SubElement(root,'Types');gn=E.SubElement(ts,'GlobalNamespace');p=E.SubElement(gn,'Program',name=program)
 if locals_:
  lv=E.SubElement(p,'LocalVars')
  for v in locals_:E.SubElement(lv,'Variable',name=v)
 main=E.SubElement(p,'MainBody');body=E.SubElement(main,'BodyContent',{X:'smcext:LdSection','name':'Logic'})
 for r in rungs:body.append(r)
 data=E.tostring(root,encoding='utf-8')
 if prefix:
  root=E.fromstring(data)
  E.cleanup_namespaces(root,top_nsmap={'iec':IEC10_NS})
  data=E.tostring(root)
 return data

def scan(rungs,**kw):
 with tempfile.TemporaryDirectory() as d:
  p=Path(d)/'source.txt';p.write_bytes(document(rungs,**kw));return collect(p)

class NativeExportTests(unittest.TestCase):
 def test_wait_pair_and_full_gate(self):
  g=scan([basic()]);self.assertEqual(g['adapter'],'omron_iec61131_10');e=g['transitions'][0]
  self.assertEqual((e['from'],e['to']),(17,29));self.assertIn('Permit',e['guard']);self.assertIn('Run',e['guard']);self.assertIn('state_wait',[e[4] for e in g['native_events']])
 def test_direct_exit_rewritten_on_complete_model(self):
  g=scan([basic()]);r=Normalizer(g).normalize({'开始标签':'Cell.Phase[7]=17','结束标签':'NOT (Cell.Phase[7]=17)'})
  self.assertEqual(r['结束标签'],'Cell.Phase[7]=29')
 def test_output_ids_scoped_to_rung(self):
  g=scan([basic(a='5',b='7'),basic(var='Other.State[9]',a='300',b='4',order=2)])
  self.assertEqual({(e['variable'],e['from'],e['to']) for e in g['transitions']},{('Cell.Phase[7]',5,7),('Other.State[9]',300,4)})
 def test_default_namespace_and_prefix_independent(self):
  self.assertEqual(scan([basic()],prefix=True)['transitions'][0]['to'],29)
 def test_literal_types_and_reversed_comparison(self):
  r=basic();sources=[n for n in r if n.get(X)=='DataSource'];sources[0].set('identifier','UINT#17');sources[1].set('identifier','Cell.Phase[7]');sources[2].set('identifier','DINT#29')
  self.assertEqual(scan([r])['transitions'][0]['from'],17)
 def test_only_actual_jump_not_number_adjacency(self):
  g=scan([basic(a='500',b='17'),basic(a='510',b='700',order=2)])
  self.assertEqual(next(e['to'] for e in g['transitions'] if e['from']==500),17)
 def test_parallel_contacts_are_or(self):
  r=basic();contact(r,'PermitB',5,10);m=next(n for n in r if n.get('typeName')=='MOVE');cp=m.find('InputVariables/InputVariable/ConnectionPointIn');E.SubElement(cp,'Connection',refConnectionPointOutId='10')
  e=scan([r])['transitions'][0];self.assertIn(' OR ',e['guard']);self.assertIn('PermitB',e['guard'])
 def test_contact_negation(self):
  r=basic();next(n for n in r if n.get('operand')=='Permit').set('negated','true');self.assertIn('NOT (Permit)',scan([r])['transitions'][0]['guard'])
 def test_branches_not_collapsed(self):
  r=basic();source(r,'41',10);block(r,'MOVE',{'EN':5,'In':10},{'ENO':11,'Out':12});sink(r,'Cell.Phase[7]',12)
  g=scan([r]);self.assertEqual({e['to'] for e in g['transitions']},{29,41})
 def test_eno_is_not_state_data(self):
  r=basic();next(n for n in r if n.get(X)=='DataSink').find('ConnectionPointIn/Connection').set('refConnectionPointOutId','8')
  self.assertFalse(scan([r])['transitions'])
 def test_disconnected_comparison_is_not_move_guard(self):
  r=basic();next(n for n in r if n.get('typeName')=='MOVE').find('InputVariables/InputVariable/ConnectionPointIn/Connection').set('refConnectionPointOutId','1')
  g=scan([r]);self.assertFalse(g['transitions']);self.assertFalse(g['stage_records'])
 def test_missing_port_retains_exit_not_proved_pair(self):
  r=basic();next(n for n in r if n.get('operand')=='Permit').find('ConnectionPointIn/Connection').set('refConnectionPointOutId','999')
  g=scan([r]);self.assertFalse(g['transitions']);self.assertTrue(g['blockers']);self.assertTrue(g['state_observations'])
 def test_duplicate_ports_are_not_guessed(self):
  r=basic();source(r,'Wrong',5);g=scan([r]);self.assertFalse(g['transitions']);self.assertTrue(g['blockers'])
 def test_edge_contact_not_level_equivalence(self):
  r=basic();next(n for n in r if n.get('operand')=='Permit').set('edge','rising');g=scan([r]);self.assertFalse(g['transitions']);self.assertTrue(g['stage_records'])
 def test_timer_path_kept_unverified(self):
  r=basic();n=next(n for n in r if n.get('operand')=='Permit');r.remove(n);block(r,'TON',{'In':5},{'Q':6},instanceName='Timer1')
  g=scan([r]);self.assertFalse(any(e['guard_complete'] for e in g['transitions']));self.assertTrue(any(s['开始标签']=='Cell.Phase[7]=17' for s in g['stage_records']))
 def test_opaque_fb_not_auto_done(self):
  r=basic();n=next(n for n in r if n.get('operand')=='Permit');r.remove(n);block(r,'CustomAction',{'EN':5},{'ENO':6})
  g=scan([r]);self.assertFalse(g['transitions']);self.assertEqual(g['stage_records'][0]['状态'],'native_connected_transition_unverified')
 def test_unrelated_command_not_attached(self):
  r=basic();block(r,'OtherMotion',{'EN':1},{'ENO':10});g=scan([r]);rows=[x for x in g['native_commands'] if x['command']=='OtherMotion'];self.assertTrue(rows);self.assertTrue(all(not x['start'] for x in rows))
 def test_local_scalar_scope(self):
  g=scan([basic(var='Phase')],locals_=['Phase']);self.assertEqual(g['transitions'][0]['binding_scope'],'Unit')
 def test_two_locals_cannot_merge_across_programs(self):
  a=E.fromstring(document([basic(var='Phase')],locals_=['Phase'],program='A'));b=E.fromstring(document([basic(var='Phase',a='17',b='45')],locals_=['Phase'],program='B'))
  a.find('{*}Types/{*}GlobalNamespace').append(b.find('{*}Types/{*}GlobalNamespace/{*}Program'))
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a.xml';p.write_bytes(E.tostring(a));g=collect(p)
  self.assertEqual({e['binding_scope'] for e in g['transitions']},{'A','B'});r=Normalizer(g).normalize({'开始标签':'Phase=17','结束标签':'NOT(Phase=17)'});self.assertIn('歧义',r['边界规范化状态'])
 def test_no_state_from_comment_or_monitor_parameter(self):
  r=basic();next(n for n in r if n.get(X)=='Comment').find('Content').text='999-注释不是步号';source(r,'1234',10);block(r,'Monitor',{'EN':1,'StartSeq':10},{'ENO':11});g=scan([r]);self.assertNotIn(999,{x['value'] for x in g['state_observations']});self.assertNotIn(1234,{x['value'] for x in g['state_observations']})
 def test_inline_st_processed_once_and_gate_retained(self):
  r=E.Element('Rung',evaluationOrder='1');cpout(node(r,'LdObject','LeftPowerRail'),1);contact(r,'Run',1,2)
  st=node(r,'LdObject','smcext:InlineST');cpin(st,2);E.SubElement(st,'ST').text='IF Cell.Phase[7]=17 AND Permit THEN Cell.Phase[7]:=29; END_IF;'
  g=scan([r]);self.assertEqual(len(g['transitions']),1);self.assertIn('Run',g['transitions'][0]['guard'])
 def test_inline_st_comments_not_executable(self):
  r=E.Element('Rung');cpout(node(r,'LdObject','LeftPowerRail'),1);st=node(r,'LdObject','smcext:InlineST');cpin(st,1);E.SubElement(st,'ST').text='(* Cell.Phase[7]:=999; *) IF Cell.Phase[7]=17 THEN Cell.Phase[7]:=29; END_IF;'
  self.assertNotIn(999,{s['value'] for s in scan([r])['state_observations']})
 def test_chained_writer_requires_scan_order(self):
  r=basic();source(r,'41',10);block(r,'MOVE',{'EN':8,'In':10},{'ENO':11,'Out':12});sink(r,'Cell.Phase[7]',12);g=scan([r]);self.assertEqual({e['to'] for e in g['transitions']},{29});self.assertTrue(any('scan_order' in b['reason'] for b in g['blockers']))
 def test_no_literal_dynamic_jump(self):
  r=basic(b='NextValue');g=scan([r]);self.assertFalse(g['transitions']);self.assertTrue(any('dynamic' in b['reason'] for b in g['blockers']))
 def test_random_names_and_values(self):
  rng=random.Random(99)
  for i in range(10):
   a,b=rng.sample(range(1,30000),2);v=f'Unit{i}.Phase[{i}]';g=scan([basic(v,str(a),str(b))]);self.assertEqual((g['transitions'][0]['variable'],g['transitions'][0]['from'],g['transitions'][0]['to']),(v,a,b))

class NativePipelineTests(unittest.TestCase):
 def test_prediction_is_filtered_after_extraction_and_keeps_two_state_controller(self):
  from agent import run
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'input.txt';p.write_bytes(document([basic(),basic('Other.Phase[1]','6','9',2)]));out=Path(d)/'out';report=run(p,out,'Cell.Phase[*]')
   with (out/'sequence_candidates.csv').open(encoding='utf-8-sig') as f:raw=list(csv.DictReader(f))
   with (out/'prediction_candidate_pairs.csv').open(encoding='utf-8-sig') as f:pairs=list(csv.DictReader(f))
   self.assertTrue(any('Other.' in r['开始标签'] for r in raw));self.assertTrue(all('Cell.Phase' in r['开始标签'] for r in pairs));self.assertTrue(any(r['结束标签']=='Cell.Phase[7]=29' for r in pairs))
   self.assertEqual(report['format'],'omron_iec61131_10')
 def test_source_graph_evaluation_adapter_and_sha(self):
  from successor_graph import write_graph
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'input.txt';p.write_bytes(document([basic()]));out=Path(d)/'xml_evidence';g=write_graph(p,out)
   self.assertEqual(g['source_sha256'],hashlib.sha256(p.read_bytes()).hexdigest());self.assertTrue((out/'native_parse_audit.json').is_file())
 def test_workflow_prepares_native_input_without_gt(self):
  from workflow import create
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'input.xml';p.write_bytes(document([basic()]));out=Path(d)/'out';m=create(p,out,pattern='Cell.Phase[*]')
   self.assertTrue(m['batches']);self.assertTrue((out/'prediction_candidate_pairs.csv').is_file());self.assertTrue(json.loads((out/'source_stage_preservation.json').read_text())['no_source_stage_pair_lost'])
 def test_unknown_dialect_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'input.xml';p.write_text('<Project xmlns="urn:unknown"><ST>Phase=1</ST></Project>')
   with self.assertRaises(ValueError):detect_source_format(p)
 def test_empty_native_project_does_not_report_success(self):
  with self.assertRaises(ValueError):scan([])
 def test_valid_native_namespace_without_omron_brand(self):
  self.assertEqual(scan([basic()])['adapter'],'omron_iec61131_10')

class NativeEvaluationIsolationTests(unittest.TestCase):
 def test_xml_assisted_evaluation_is_read_only_and_one_pair(self):
  from plc_cli import evaluate
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);source=d/'source.xml';source.write_bytes(document([basic()]));pred=d/'pred.csv';gt=d/'truth.csv'
   pred.write_text('开始标签,结束标签\nCell.Phase[7]=17,Cell.Phase[7]=29\n',encoding='utf-8')
   gt.write_text('开始标签,结束标签\nCell.Phase[7]=17,NOT(Cell.Phase[7]=17)\nCell.Phase[7]=17,NOT(Cell.Phase[7]=17)\n',encoding='utf-8')
   before=pred.read_bytes();result=evaluate(SimpleNamespace(prediction=str(pred),gt=str(gt),xml=str(source),relations=None,transition_graph=None,out=str(d/'eval'),top_k=5,no_excel=True))
   self.assertEqual(result['unique_scored_gt'],1);self.assertEqual(result['additional_source_model_matches'],1);self.assertEqual(before,pred.read_bytes())
 def test_prediction_does_not_consume_neighbor_gt(self):
  from agent import run
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);source=d/'source.xml';source.write_bytes(document([basic()]));(d/'gt.csv').write_text('开始标签,结束标签\nCell.Phase[7]=99999,Cell.Phase[7]=88888\n')
   run(source,d/'out','Cell.Phase[*]')
   self.assertNotIn('99999',(d/'out'/'sequence_candidates.csv').read_text(encoding='utf-8-sig'))
