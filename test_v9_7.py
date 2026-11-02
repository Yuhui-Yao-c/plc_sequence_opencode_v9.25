import csv,json,tempfile,unittest
from pathlib import Path
from boundary_logic import parse,canonical,exit_states,BoundarySyntaxError
from boundary_normalizer import Normalizer,normalize_rows,RESOLVED
from successor_graph import collect

def edge(a,b,var='Cell.Phase[4]',scope='',guard=None):
 return {'id':f'{var}:{a}>{b}:{guard or ""}','scope':scope or 'Demo','binding_scope':scope,'variable':var,'from':a,'to':b,'guard':guard or f'{var}={a} AND Done','guard_complete':True,'evidence':'st_structured_guard','path':['fixture']}
def graph(*es,blockers=()):return {'schema':'plc.state_transitions.v1','transitions':list(es),'blockers':list(blockers)}
def row(a='Cell.Phase[4]=500',b='NOT(Cell.Phase[4]=500)'):
 return {'开始标签':a,'结束标签':b,'动作':'移动'}

def ld_xml(extra='',gate='4',target='530'):
 return f'''<project><pou name="Demo"><body><LD>
 <leftPowerRail localId="0"/>
 <inVariable localId="1"><expression>Cell.Phase[4]</expression></inVariable>
 <inVariable localId="2"><expression>500</expression></inVariable>
 <block localId="3" typeName="EQ"><inputVariables>
 <variable formalParameter="IN1"><connectionPointIn><connection refLocalId="1"/></connectionPointIn></variable>
 <variable formalParameter="IN2"><connectionPointIn><connection refLocalId="2"/></connectionPointIn></variable>
 </inputVariables><outputVariables><variable formalParameter="OUT"/></outputVariables></block>
 <contact localId="4"><connectionPointIn><connection refLocalId="3" formalParameter="OUT"/></connectionPointIn><variable>Axis.Arrived</variable></contact>
 {extra}
 <inVariable localId="5"><expression>{target}</expression></inVariable>
 <block localId="6" typeName="MOVE"><inputVariables>
 <variable formalParameter="EN"><connectionPointIn><connection refLocalId="{gate}"/></connectionPointIn></variable>
 <variable formalParameter="IN"><connectionPointIn><connection refLocalId="5"/></connectionPointIn></variable>
 </inputVariables><outputVariables><variable formalParameter="OUT"><connectionPointOut/></variable></outputVariables></block>
 <outVariable localId="7"><connectionPointIn><connection refLocalId="6" formalParameter="OUT"/></connectionPointIn><expression>Cell.Phase[4]</expression></outVariable>
 </LD></body></pou></project>'''

def scan_text(xml):
 with tempfile.TemporaryDirectory() as d:
  p=Path(d)/'x.xml';p.write_text(xml,encoding='utf-8');return collect(p)

def st(code):return '<project><pou name="Demo"><body><ST><xhtml xmlns="http://www.w3.org/1999/xhtml">'+code.replace('&','&amp;').replace('<','&lt;')+'</xhtml></ST></body></pou></project>'

class BooleanGrammar(unittest.TestCase):
 def test_de_morgan(self):
  self.assertEqual(canonical('NOT(S=INT#12 OR S=14)'),canonical('NOT S=12 AND NOT S=14'))
 def test_order(self):self.assertEqual(canonical('A AND S=2'),canonical('(s=INT#2) AND a'))
 def test_no_substring_collision(self):self.assertNotEqual(canonical('Brand=1'),canonical('Br AND =1'))
 def test_shorthand_rejected(self):
  with self.assertRaises(BoundarySyntaxError):parse('S=1 OR 2')
 def test_wrong_not_or(self):self.assertIsNone(exit_states(parse('NOT S=1 OR NOT S=2')))
 def test_compound_symbol(self):self.assertEqual(parse('M[2,3].Next[1]=INT#500').args,('M[2,3].Next[1]',500))

class FirstExitNormalization(unittest.TestCase):
 def test_direct_not_numeric_successor(self):
  r=Normalizer(graph(edge(500,620),edge(510,520))).normalize(row())
  self.assertEqual(r['结束标签'],'Cell.Phase[4]=620')
 def test_decreasing_successor(self):
  self.assertEqual(Normalizer(graph(edge(500,20))).normalize(row())['结束标签'],'Cell.Phase[4]=20')
 def test_branch_or_one_row(self):
  rs,stat=normalize_rows([row()],graph(edge(500,620),edge(500,0)))
  self.assertEqual(len(rs),1);self.assertIn(' OR ',rs[0]['结束标签']);self.assertTrue(stat['row_count_preserved'])
 def test_region_first_exit(self):
  g=graph(edge(2910,2960),edge(2960,3100),edge(3100,3200))
  r=Normalizer(g).normalize(row('Cell.Phase[4]=2910','NOT Cell.Phase[4]=2910 AND NOT Cell.Phase[4]=2960'))
  self.assertEqual(r['结束标签'],'Cell.Phase[4]=3100')
  self.assertEqual(r['边界规范化状态'],'已规范化-区域首出口')
 def test_no_distant_convergence(self):
  r=Normalizer(graph(edge(500,620),edge(500,630),edge(620,700),edge(630,700))).normalize(row())
  self.assertNotIn('700',r['结束标签']);self.assertIn('630',r['结束标签'])
 def test_disconnected_excluded_state(self):
  r=Normalizer(graph(edge(2910,3100))).normalize(row('Cell.Phase[4]=2910','NOT(Cell.Phase[4]=2910 OR Cell.Phase[4]=2960)'))
  self.assertEqual(r['结束标签'],'Cell.Phase[4]=3100')
 def test_all_or_starts_required(self):
  r=Normalizer(graph(edge(2910,3100))).normalize(row('Cell.Phase[4]=2910 OR Cell.Phase[4]=2960','NOT(Cell.Phase[4]=2910 OR Cell.Phase[4]=2960)'))
  self.assertNotIn(r['边界规范化状态'],RESOLVED)
 def test_missing_evidence_preserves(self):
  r=Normalizer(graph()).normalize(row());self.assertEqual(r['结束标签'],row()['结束标签'])
 def test_dynamic_writer_blocks(self):
  g=graph(edge(500,620),blockers=[{'variable':'Cell.Phase[4]','from':None,'reason':'dynamic'}])
  self.assertNotIn(Normalizer(g).normalize(row())['边界规范化状态'],RESOLVED)
 def test_loop_without_exit(self):
  self.assertNotIn(Normalizer(graph(edge(500,500))).normalize(row())['边界规范化状态'],RESOLVED)
 def test_feedback_not_replaced(self):
  r=Normalizer(graph(edge(500,620))).normalize(row(b='Cell.Phase[4]=500 AND Axis.Arrived'))
  self.assertEqual(r['结束标签'],'Cell.Phase[4]=500 AND Axis.Arrived');self.assertEqual(r['边界规范化状态'],'无需转换')
 def test_scope_ambiguity(self):
  g=graph(edge(500,620,'S','P1'),edge(500,630,'S','P2'))
  self.assertEqual(Normalizer(g).normalize(row('S=500','NOT S=500'))['边界规范化状态'],'待核验-变量作用域歧义')
 def test_scoped_binding(self):
  r=Normalizer(graph(edge(500,620,'S','P1'),edge(500,630,'S','P2'))).normalize({**row('S=500','NOT S=500'),'证据POU':'P1'})
  self.assertEqual(r['结束标签'],'S=620')
 def test_co_location_not_proof(self):
  e=edge(500,620);e['evidence']='co_location_only'
  self.assertNotIn(Normalizer(graph(e)).normalize(row())['边界规范化状态'],RESOLVED)

class SourceGraph(unittest.TestCase):
 def test_contact_and_real_move_target(self):
  g=scan_text(ld_xml());self.assertEqual(len(g['transitions']),1)
  self.assertEqual(g['transitions'][0]['to'],530);self.assertIn('Axis.Arrived',g['transitions'][0]['guard'])
 def test_missing_ref_is_blocker(self):
  g=scan_text(ld_xml(gate='999'));self.assertFalse(g['transitions']);self.assertTrue(g['blockers'])
 def test_negated_contact(self):
  g=scan_text(ld_xml().replace('<contact localId="4">','<contact localId="4" negated="true">'))
  self.assertIn('NOT (Axis.Arrived)',g['transitions'][0]['guard'])
 def test_eq_enable_chain(self):
  xml=ld_xml().replace('<variable formalParameter="IN1">','<variable formalParameter="EN"><expression>AutoRun</expression></variable><variable formalParameter="IN1">')
  g=scan_text(xml);self.assertIn('AutoRun',g['transitions'][0]['guard'])
 def test_eno_not_output(self):
  g=scan_text(ld_xml().replace('refLocalId="3" formalParameter="OUT"','refLocalId="3" formalParameter="ENO"'))
  self.assertFalse(g['transitions']);self.assertTrue(g['blockers'])
 def test_st_case(self):
  g=scan_text(st('CASE Cell.Phase[4] OF 500: IF Axis.Arrived THEN Cell.Phase[4]:=620; END_IF; 620: Cell.Phase[4]:=20; END_CASE;'))
  self.assertEqual({(e['from'],e['to']) for e in g['transitions']},{(500,620),(620,20)})
 def test_st_elsif_guards(self):
  g=scan_text(st('IF Cell.Phase[4]=500 THEN IF OK THEN Cell.Phase[4]:=620; ELSIF NG THEN Cell.Phase[4]:=30; ELSE Cell.Phase[4]:=10; END_IF; END_IF;'))
  self.assertEqual(len(g['transitions']),3)
  self.assertTrue(any('NOT (OK)' in e['guard'] and 'NG' in e['guard'] for e in g['transitions']))
 def test_st_dynamic_target(self):
  g=scan_text(st('IF Cell.Phase[4]=500 THEN Cell.Phase[4]:=Requested; END_IF;'))
  self.assertTrue(g['blockers']);self.assertFalse(g['transitions'])
 def test_smc2_no_false_support(self):
  g=scan_text('<smc2><entry type="json">{"CLs":[]}</entry></smc2>')
  self.assertEqual(g['adapter'],'smc2');self.assertFalse(g['transitions']);self.assertTrue(g['warnings'])
 def test_command_not_completion(self):
  g=scan_text(st('CASE Cell.Phase[4] OF 500: Motor.Execute:=TRUE; IF Done THEN Cell.Phase[4]:=620; END_IF; END_CASE;'))
  self.assertEqual(len(g['observations']),1);self.assertEqual(len(g['transitions']),1)
if __name__=='__main__':unittest.main()

class IntegrationAndEvaluation(unittest.TestCase):
 def test_repeated_normalization_keeps_evidence(self):
  n=Normalizer(graph(edge(500,620)));r=n.normalize(row());r2=n.normalize(r)
  self.assertEqual(r,r2)
 def test_empty_original_metadata_not_lost(self):
  r=Normalizer(graph(edge(500,620))).normalize({**row(),'原始开始标签':'','原始结束标签':''})
  self.assertEqual(r['原始结束标签'],row()['结束标签'])
 def test_evaluation_symmetric_and_pair_only(self):
  from pair_evaluation import evaluate
  p=[row(b='Cell.Phase[4]=620')];g=[row()];r,_,m,_,_=evaluate(p,g,graph(edge(500,620)))
  self.assertEqual(r[0]['最佳匹配'],'状态模型边界命中');self.assertEqual(m['additional_source_model_matches'],1)
 def test_do_not_splice_rows(self):
  from pair_evaluation import evaluate
  p=[row('S=1','S=2'),row('S=2','S=3')];g=[row('S=1','S=3')]
  self.assertEqual(evaluate(p,g)[2]['unique_scored_gt'],0)
 def test_duplicate_alias_capacity_one(self):
  from pair_evaluation import evaluate
  p=[row(),row(b='Cell.Phase[4]=620')];g=[row(),row(b='Cell.Phase[4]=620')]
  self.assertEqual(evaluate(p,g,graph(edge(500,620)))[2]['unique_scored_gt'],1)
 def test_malformed_not_counted(self):
  from pair_evaluation import evaluate
  bad=row('S=1 OR 2','S=3');r,_,m,_,_=evaluate([bad],[bad])
  self.assertEqual(m['unique_scored_gt'],0);self.assertEqual(r[0]['最佳匹配'],'GT写法待核验')
 def test_multiline_truth_and_repeated_header(self):
  from table_io import truth_records
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'gt.tsv';p.write_text('动作描述\t开始标签\t结束标签\n等待\t"S=1\nAND Ready"\tS=2\n机构动作\tA\t\n\t开始\t结束\n动作二\tS=2\tS=3\n',encoding='utf-8')
   rr=truth_records(p);self.assertEqual(len(rr),2);self.assertEqual(rr[1]['动作描述'],'动作二');self.assertIn('\n',rr[0]['开始标签'])
 def test_final_pools_never_shrink(self):
  from workflow import create,validate,csv_read
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'src.xml';p.write_text(ld_xml(),encoding='utf-8');out=Path(d)/'run';meta=create(p,out,pattern='Cell.Phase[*]')
   self.assertEqual(len(csv_read(out/'prediction_candidate_pairs_raw.csv')),len(csv_read(out/'prediction_candidate_pairs.csv')))
   for b in meta['batches']:
    inp=json.loads((out/b['input']).read_text())
    rr={'batch_id':b['batch_id'],'steps':[], 'unresolved_events':[{'event_id':x['事件ID'],'reason':'synthetic test no LLM'} for x in inp['events']]}
    (out/b['output']).write_text(json.dumps(rr))
   status=validate(out,True)
   self.assertTrue(status['success']);self.assertEqual(len(csv_read(out/'final_prediction_pairs_full.csv')),meta['candidate_pairs_count'])
   # v9.10 changes the public view to deduplicated atomic rows; retention is
   # checked against the lossless archive and per-input lineage, not display size.
   self.assertEqual(len(csv_read(out/'final_atomic_lineage.csv')),meta['candidate_pairs_count'])
 def test_process_contract_no_keyword_shortcut(self):
  from process_contract import review
  verdict=review({'动作':'等待轴运动','开始标签':'S=1','结束标签':'S=2','证据候选ID':'C'}, {'C':{'状态':'adjacency_hypothesis'}},{})
  self.assertEqual(verdict['工艺契约结果'],'待补充证据')
 def test_process_contract_explicit_answers(self):
  from process_contract import review,FIELDS
  r={**row(),'过程类型':'机械动作','证据候选ID':'C'}
  r.update({k:'Synthetic test explanation' for k in FIELDS[1:]})
  self.assertEqual(review(r,{'C':{'状态':'source_supported_transition'}},{})['工艺契约结果'],'推荐待复核')
 def test_regex_filter(self):
  from candidate_filter import match_boundary
  self.assertTrue(match_boundary('Unit12.Stage[3]=1','Ready',r're:Unit\d+\.Stage\[\d+\]'))
 def test_unknown_grammar_no_state_guess(self):
  n=Normalizer(graph(edge(500,620)))
  self.assertEqual(n.normalize(row('Cell.Phase[4]=500 AND Permit'))['边界规范化状态'],'待核验-起点与退出集合不一致')

class BranchCompleteness(unittest.TestCase):
 def test_or_unconstrained_writer_blocks(self):
  g=scan_text(st('IF Cell.Phase[4]=500 OR Emergency THEN Cell.Phase[4]:=620; END_IF;'))
  self.assertTrue(any(b['reason']=='guard_has_unconstrained_source_branch' for b in g['blockers']))
  self.assertNotIn(Normalizer(g).normalize(row())['边界规范化状态'],RESOLVED)
