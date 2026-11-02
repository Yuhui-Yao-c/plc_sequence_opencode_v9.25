import copy,csv,json,tempfile,unittest,hashlib
from pathlib import Path
from atomic_boundaries import AtomicProjector,export_atomic,atomic
from atomic_evaluation import evaluate
from boundary_normalizer import read_csv

def edge(a,b,var='Cell.Phase[7]',scope='',guard=None):
    return {'id':f'{scope}:{var}:{a}>{b}:{guard or ""}','variable':var,'from':a,'to':b,
            'scope':scope or 'Program::Action','binding_scope':scope,
            'guard':guard or f'{var}={a} AND Permit','guard_complete':True,
            'evidence':'st_structured_guard','path':['example']}
def graph(*es,blocks=()):
    return {'schema':'plc.state_transitions.v1','transitions':list(es),'blockers':list(blocks)}
def row(a,b):return {'开始标签':a,'结束标签':b}
def r(a,b):return row(f'Cell.Phase[7]={a}',b)

class AtomicOutputTests(unittest.TestCase):
    def test_skip_numeric_neighbor(self):
        p=AtomicProjector(graph(edge(12,900),edge(13,14))).project(r(12,'NOT Cell.Phase[7]=12'))
        self.assertEqual(p.rows[0]['结束标签'],'Cell.Phase[7]=900');self.assertTrue(p.strict_equivalence)
    def test_decreasing_successor(self):
        p=AtomicProjector(graph(edge(900,0))).project(r(900,'NOT Cell.Phase[7]=900'))
        self.assertEqual(p.rows[0]['结束标签'],'Cell.Phase[7]=0')
    def test_region_first_exit(self):
        p=AtomicProjector(graph(edge(30,50),edge(50,7),edge(7,1))).project(r(30,'NOT Cell.Phase[7]=30 AND NOT Cell.Phase[7]=50'))
        self.assertEqual([(x['开始标签'],x['结束标签']) for x in p.rows],[('Cell.Phase[7]=30','Cell.Phase[7]=7')])
        self.assertTrue(p.strict_equivalence)
    def test_different_disjoint_branches(self):
        p=AtomicProjector(graph(edge(30,32),edge(50,55))).project(row('Cell.Phase[7]=30 OR Cell.Phase[7]=50','NOT (Cell.Phase[7]=30 OR Cell.Phase[7]=50)'))
        self.assertEqual({(x['开始标签'],x['结束标签']) for x in p.rows},{('Cell.Phase[7]=30','Cell.Phase[7]=32'),('Cell.Phase[7]=50','Cell.Phase[7]=55')})
    def test_same_exit_preserves_two_starts(self):
        p=AtomicProjector(graph(edge(30,80),edge(50,80))).project(row('Cell.Phase[7]=30 OR Cell.Phase[7]=50','NOT (Cell.Phase[7]=30 OR Cell.Phase[7]=50)'))
        self.assertEqual(len(p.rows),2) # common end does not make two starts equal
    def test_multiple_exits_not_distant_merge(self):
        p=AtomicProjector(graph(edge(10,20),edge(10,30),edge(20,90),edge(30,90))).project(r(10,'NOT Cell.Phase[7]=10'))
        self.assertEqual({x['结束标签'] for x in p.rows},{'Cell.Phase[7]=20','Cell.Phase[7]=30'})
    def test_per_branch_trace(self):
        p=AtomicProjector(graph(edge(10,20),edge(10,30))).project(r(10,'NOT Cell.Phase[7]=10'))
        self.assertEqual({e['to'] for e in p.paths[0]['edges']},{20})
    def test_internal_or_start_needs_latch(self):
        p=AtomicProjector(graph(edge(10,20),edge(20,30))).project(row('Cell.Phase[7]=10 OR Cell.Phase[7]=20','NOT (Cell.Phase[7]=10 OR Cell.Phase[7]=20)'))
        self.assertEqual(p.status,'OR集合入口待核验');self.assertFalse(p.rows)
    def test_missing_or_exit_conditional(self):
        p=AtomicProjector(graph(edge(10,11))).project(row('Cell.Phase[7]=10 OR Cell.Phase[7]=20','NOT (Cell.Phase[7]=10 OR Cell.Phase[7]=20)'))
        self.assertFalse(p.strict_equivalence);self.assertTrue(any('未覆盖OR入口' in x for x in p.issues))
    def test_unknown_writer_does_not_destroy_local_candidate(self):
        p=AtomicProjector(graph(edge(10,11),blocks=[{'variable':'Cell.Phase[7]','from':None,'reason':'unknown_reset'}])).project(r(10,'NOT Cell.Phase[7]=10'))
        self.assertEqual(p.rows[0]['结束标签'],'Cell.Phase[7]=11');self.assertFalse(p.strict_equivalence)
        self.assertIn('unknown_reset',' '.join(p.issues))
    def test_unverified_edge_not_proof(self):
        e=edge(10,11);e['guard_complete']=False
        self.assertFalse(AtomicProjector(graph(e)).project(r(10,'NOT Cell.Phase[7]=10')).rows)
    def test_and_not_dropped(self):
        p=AtomicProjector(graph(edge(10,11))).project(row('Cell.Phase[7]=10 AND Axis.Arrived','NOT Cell.Phase[7]=10'))
        self.assertFalse(p.rows);self.assertEqual(p.status,'复合起点待核验')
    def test_wrong_controller_not_corrected(self):
        p=AtomicProjector(graph(edge(10,11))).project(r(10,'NOT Cell.Phase[8]=10'))
        self.assertFalse(p.rows)
    def test_shorthand_not_silently_corrected(self):
        p=AtomicProjector(graph(edge(10,11))).project(row('Cell.Phase[7]=10 OR 20','NOT Cell.Phase[7]=10'))
        self.assertFalse(p.rows);self.assertEqual(p.status,'写法待核验')
    def test_no_cartesian_for_explicit_ors(self):
        p=AtomicProjector(graph(edge(10,11),edge(20,21))).project(row('Cell.Phase[7]=10 OR Cell.Phase[7]=20','Cell.Phase[7]=11 OR Cell.Phase[7]=21'))
        self.assertEqual(len(p.rows),2)
        self.assertNotIn(('Cell.Phase[7]=10','Cell.Phase[7]=21'),[(x['开始标签'],x['结束标签']) for x in p.rows])
    def test_explicit_overlap_needs_cycle_definition(self):
        p=AtomicProjector(graph(edge(10,11))).project(row('Cell.Phase[7]=10 OR Cell.Phase[7]=20','Cell.Phase[7]=10 OR Cell.Phase[7]=11'))
        self.assertEqual(p.status,'周期待核验')
    def test_cycle_without_exit_review(self):
        p=AtomicProjector(graph(edge(10,20),edge(20,10))).project(r(10,'NOT (Cell.Phase[7]=10 OR Cell.Phase[7]=20)'))
        self.assertFalse(p.rows)
    def test_scope_collision_no_cross_pou(self):
        p=AtomicProjector(graph(edge(10,11,scope='P'),edge(10,21,scope='Q'))).project(r(10,'NOT Cell.Phase[7]=10'))
        self.assertFalse(p.rows);self.assertEqual(p.status,'作用域待核验')
    def test_scope_disambiguation(self):
        rr=r(10,'NOT Cell.Phase[7]=10');rr['证据POU']='Q::Body'
        p=AtomicProjector(graph(edge(10,11,scope='P'),edge(10,21,scope='Q'))).project(rr)
        self.assertEqual(p.rows[0]['结束标签'],'Cell.Phase[7]=21')
    def test_parent_metadata_cannot_replace_actual_cells(self):
        rr=row('S=10','S=20');rr['原始开始标签']='Bad=9';rr['原始结束标签']='NOT Bad=9'
        p=AtomicProjector().project(rr);self.assertEqual(p.rows[0]['开始标签'],'S=10')
    def test_budget_no_truncation(self):
        p=AtomicProjector(graph(edge(10,11),edge(10,12)),max_pairs=1).project(r(10,'NOT Cell.Phase[7]=10'))
        self.assertFalse(p.rows);self.assertEqual(p.status,'展开上限待核验')
    def test_raw_preservation_and_atomic_guarantee(self):
        rows=[r(10,'NOT Cell.Phase[7]=10'),r(10,'Cell.Phase[7]=11'),row('S=9 AND Ready','S=10')]
        old=copy.deepcopy(rows)
        with tempfile.TemporaryDirectory() as d:
            st=export_atomic(rows,graph(edge(10,11)),d)
            self.assertEqual(rows,old);self.assertEqual(st['input_complete_pairs'],3)
            self.assertEqual(st['rows_with_atomic_output'],2);self.assertEqual(st['rows_without_atomic_output'],1)
            self.assertTrue(st['every_input_accounted']);self.assertEqual(st['unique_atomic_pairs'],1)
            rr,_=read_csv(Path(d)/'prediction_atomic_pairs.csv')
            self.assertTrue(all(atomic(x['开始标签']) and atomic(x['结束标签']) for x in rr))
    def test_deterministic_exports(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);g=graph(edge(10,11))
            export_atomic([r(10,'NOT Cell.Phase[7]=10')],g,p/'a')
            export_atomic([r(10,'NOT Cell.Phase[7]=10')],g,p/'b')
            for name in ('prediction_atomic_pairs.csv','prediction_atomic_conditions.json'):
                self.assertEqual((p/'a'/name).read_bytes(),(p/'b'/name).read_bytes())

class AtomicEvaluationTests(unittest.TestCase):
    def test_strict_atomic_single_pair(self):
        rr,_,m,_=evaluate([row('S=1','S=2')],[row('S=INT#1','S=INT#2')])
        self.assertEqual(m['unique_scored_gt'],1)
    def test_global_exit_proof(self):
        rr,_,m,_=evaluate([r(10,'Cell.Phase[7]=11')],[r(10,'NOT Cell.Phase[7]=10')],graph(edge(10,11)))
        self.assertEqual(m['additional_source_model_matches'],1)
    def test_conditional_not_scored(self):
        rr,_,m,_=evaluate([r(10,'Cell.Phase[7]=11')],[r(10,'NOT Cell.Phase[7]=10')],graph(edge(10,11),blocks=[{'variable':'Cell.Phase[7]','from':None,'reason':'reset'}]))
        self.assertEqual(m['unique_scored_gt'],0);self.assertEqual(rr[0]['最佳匹配'],'有条件的迁移替代')
    def test_or_one_branch_cannot_cover_all(self):
        rr,_,m,dd= evaluate([r(10,'Cell.Phase[7]=11')],[row('Cell.Phase[7]=10 OR Cell.Phase[7]=20','NOT(Cell.Phase[7]=10 OR Cell.Phase[7]=20)')],graph(edge(10,11),edge(20,21)))
        self.assertEqual(m['unique_scored_gt'],0);self.assertEqual(rr[0]['最佳匹配'],'复合分支部分命中')
    def test_or_complete_only_diagnostic(self):
        rr,_,m,dd=evaluate([r(10,'Cell.Phase[7]=11'),r(20,'Cell.Phase[7]=21')],[row('Cell.Phase[7]=10 OR Cell.Phase[7]=20','NOT(Cell.Phase[7]=10 OR Cell.Phase[7]=20)')],graph(edge(10,11),edge(20,21)))
        self.assertEqual(m['unique_scored_gt'],0);self.assertEqual(rr[0]['最佳匹配'],'复合分支齐全-仅诊断')
    def test_duplicate_gt_capacity_one(self):
        _,_,m,_=evaluate([row('S=1','S=2'),row('S=INT#1','S=2')],[row('S=1','S=2'),row('S=1','S=2')])
        self.assertEqual(m['unique_scored_gt'],1);self.assertEqual(m['categories']['一对一匹配冲突'],1)
    def test_no_splicing(self):
        _,_,m,_=evaluate([row('S=1','S=2'),row('S=2','S=3')],[row('S=1','S=3')])
        self.assertEqual(m['unique_scored_gt'],0)
    def test_nonatomic_input_rejected(self):
        with self.assertRaises(ValueError):evaluate([row('S=1','NOT S=1')],[])
    def test_no_gt_based_prediction_mutation(self):
        p=[row('S=1','S=2')];old=copy.deepcopy(p)
        evaluate(p,[row('S=9','S=10')]);self.assertEqual(p,old)


class WorkflowAtomicTests(unittest.TestCase):
    def test_finish_public_tables_are_atomic_and_archives_remain(self):
        from workflow import csv_write,validate
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'batches').mkdir()
            c={'候选ID':'C1','开始标签':'S=10','结束标签':'NOT S=10','状态':'source_supported_transition'}
            e={'事件ID':'E1','guard':'S=10','evidence':'st_structured_guard'}
            csv_write(p/'indexed_candidates.csv',[c],list(c));csv_write(p/'indexed_events.csv',[e],list(e))
            csv_write(p/'prediction_candidate_pairs.csv',[c],list(c))
            (p/'source_transition_graph.json').write_text(json.dumps(graph(edge(10,20,var='S'))))
            (p/'workflow_manifest.json').write_text(json.dumps({'batches':[{'batch_id':'B1','output':'batches/B1.result.json','candidate_ids':['C1'],'event_ids':['E1']}]}))
            st={**row('S=10','NOT S=10'),'证据候选ID':['C1'],'证据事件ID':['E1'],'过程类型':'工艺等待','统计对象':'等待工位','起点含义':'进入等待','终点含义':'许可达成','工艺依据':'source:rung1','边界依据':'source:rung1'}
            (p/'batches/B1.result.json').write_text(json.dumps({'batch_id':'B1','steps':[st],'unresolved_events':[]}))
            self.assertTrue(validate(p,write=True)['success'])
            for name in ('final_prediction_pairs.csv','final_sequence.csv','final_process_steps.csv'):
                rr,_=read_csv(p/name)
                self.assertTrue(rr)
                self.assertTrue(all(atomic(r['开始标签']) and atomic(r['结束标签']) for r in rr))
            for name in ('final_prediction_pairs_full.csv','final_sequence_raw.csv','final_process_steps_raw.csv'):
                self.assertTrue((p/name).is_file())
            rr,_=read_csv(p/'final_process_steps.csv');self.assertEqual(rr[0]['统计对象'],'等待工位')
    def test_skill_registered_and_no_project_literals(self):
        root=Path(__file__).parent
        skill=root/'.opencode/skills/plc-boundary-normalization/SKILL.md'
        self.assertTrue(skill.exists());text=skill.read_text(encoding='utf-8')
        self.assertIn('name: plc-boundary-normalization',text)
        self.assertIn('AND',text);self.assertIn('首次',text)
        for name in ('atomic_boundaries.py','atomic_evaluation.py'):
            code=(root/name).read_text(encoding='utf-8')
            self.assertNotIn('m_nNextAutoStep',code)
            self.assertNotIn('ST17.',code)
    def test_local_candidate_source_has_no_gt_argument(self):
        import inspect
        self.assertNotIn('gt',inspect.signature(export_atomic).parameters)
        self.assertNotIn('truth',inspect.signature(AtomicProjector).parameters)

if __name__=='__main__':unittest.main()
