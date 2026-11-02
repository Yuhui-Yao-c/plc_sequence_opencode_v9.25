"""Generic feedback-to-direct-successor tests; no production project constants."""
import copy
import inspect
import json
import random
import tempfile
import unittest
from pathlib import Path
from feedback_successor import FeedbackResolver
from atomic_boundaries import AtomicProjector, export_atomic, atomic
from atomic_evaluation import evaluate
from pair_evaluation import evaluate as evaluate_full
from boundary_normalizer import read_csv


def edge(src=70, dest=4, guard='Cell.Phase[2]=70 AND Drive.InPlace[4]', **kw):
    return {'id':f'E{src}_{dest}', 'variable':'Cell.Phase[2]', 'from':src, 'to':dest,
            'scope':'Flow', 'binding_scope':'', 'guard':guard, 'guard_complete':True,
            'evidence':'plcopen_wired_guard', **kw}


def graph(*edges, blocks=None):
    return {'schema':'plc.state_transitions.v1', 'transitions':list(edges), 'blockers':blocks or []}


def row(start='Cell.Phase[2]=70', end='Cell.Phase[2]=70 AND Drive.InPlace[4]', **kw):
    return {'开始标签':start, '结束标签':end, **kw}


class FeedbackTests(unittest.TestCase):
    def test_backward_direct_not_numeric_next(self):
        r=FeedbackResolver(graph(edge(),edge(70,100,'Cell.Phase[2]=70 AND Other'))).resolve(row())
        self.assertEqual([b['结束标签'] for b in r.rows],['Cell.Phase[2]=4'])
    def test_extra_gates_preserved(self):
        r=FeedbackResolver(graph(edge(guard='Cell.Phase[2]=70 AND Drive.InPlace[4] AND Run AND Single'))).resolve(row())
        self.assertIn('Run',r.rows[0]['附加门控']);self.assertIn('Single',r.rows[0]['附加门控'])
        self.assertFalse(r.paths[0]['feedback_relations'][0]['feedback_sufficient_at_source'])
    def test_no_boolean_equivalence_even_exact_guard(self):
        r=AtomicProjector(graph(edge())).project(row())
        self.assertTrue(r.rows);self.assertFalse(r.strict_equivalence)
    def test_no_graph_no_guess(self):
        r=FeedbackResolver(graph()).resolve(row());self.assertFalse(r.rows)
    def test_unknown_guard_no_projection(self):
        r=FeedbackResolver(graph(edge(guard_complete=False))).resolve(row());self.assertFalse(r.rows)
    def test_wrong_controller_no_cross_link(self):
        r=FeedbackResolver(graph(edge(variable='Other.Phase'))).resolve(row());self.assertFalse(r.rows)
    def test_guard_bypasses_feedback(self):
        r=FeedbackResolver(graph(edge(guard='Cell.Phase[2]=70 AND (Drive.InPlace[4] OR Bypass)'))).resolve(row())
        self.assertFalse(r.rows)
    def test_or_with_required_feedback(self):
        g='Cell.Phase[2]=70 AND ((Run AND Drive.InPlace[4]) OR (Manual AND Drive.InPlace[4]))'
        r=FeedbackResolver(graph(edge(guard=g))).resolve(row());self.assertTrue(r.rows)
        self.assertIn('Run',r.rows[0]['附加门控']);self.assertIn('Manual',r.rows[0]['附加门控'])
    def test_negated_feedback_does_not_end_on_positive(self):
        r=FeedbackResolver(graph(edge(guard='Cell.Phase[2]=70 AND NOT Drive.InPlace[4]'))).resolve(row())
        self.assertFalse(r.rows)
    def test_only_other_feedback_not_enough(self):
        r=FeedbackResolver(graph(edge(guard='Cell.Phase[2]=70 AND Drive.InPlace[5]'))).resolve(row())
        self.assertFalse(r.rows)
    def test_multiple_feedbacks_all_required(self):
        rr=row(end='Cell.Phase[2]=70 AND A.Done AND B.Done')
        self.assertFalse(FeedbackResolver(graph(edge(guard='Cell.Phase[2]=70 AND A.Done'))).resolve(rr).rows)
        self.assertTrue(FeedbackResolver(graph(edge(guard='Cell.Phase[2]=70 AND A.Done AND B.Done'))).resolve(rr).rows)
    def test_multiple_destinations_not_single_scored(self):
        g=graph(edge(),edge(70,8,guard='Cell.Phase[2]=70 AND Drive.InPlace[4] AND Route'))
        r=AtomicProjector(g).project(row());self.assertEqual(len(r.rows),2)
        _,_,m,_=evaluate([row(end='Cell.Phase[2]=4')],[row()],g)
        self.assertEqual(m['unique_scored_gt'],0)
    def test_no_transitive_skip(self):
        g=graph(edge(),edge(4,9,'Cell.Phase[2]=4 AND Done'))
        r=FeedbackResolver(g).resolve(row());self.assertEqual(r.rows[0]['结束标签'],'Cell.Phase[2]=4')
    def test_self_loop_not_successor(self):
        self.assertFalse(FeedbackResolver(graph(edge(dest=70))).resolve(row()).rows)
    def test_conflicting_guard_not_executable(self):
        self.assertFalse(FeedbackResolver(graph(edge(guard='Cell.Phase[2]=70 AND Drive.InPlace[4] AND NOT Drive.InPlace[4]'))).resolve(row()).rows)
    def test_missing_source_guard_rejected(self):
        self.assertFalse(FeedbackResolver(graph(edge(guard='Drive.InPlace[4]'))).resolve(row()).rows)
    def test_scope_collision_blocks(self):
        g=graph(edge(binding_scope='P1'),edge(binding_scope='P2',dest=9))
        self.assertFalse(FeedbackResolver(g).resolve(row()).rows)
        self.assertEqual(FeedbackResolver(g).resolve(row(证据POU='P2')).rows[0]['结束标签'],'Cell.Phase[2]=9')
    def test_reset_blockers_are_not_discarded(self):
        g=graph(edge(),blocks=[{'variable':'Cell.Phase[2]','from':None,'reason':'external_reset'}])
        r=FeedbackResolver(g).resolve(row());self.assertTrue(r.rows)
        self.assertIn('external_reset',' '.join(r.issues))
    def test_composite_cross_state_untouched(self):
        self.assertFalse(FeedbackResolver(graph(edge())).resolve(row(end='Cell.Phase[2]=70 AND AnotherStep=2')).applicable)
    def test_compound_start_untouched(self):
        self.assertFalse(FeedbackResolver(graph(edge())).resolve(row(start='Cell.Phase[2]=70 AND StartPermit')).applicable)
    def test_typed_integer_commutative_end(self):
        r=FeedbackResolver(graph(edge())).resolve(row(end='Drive.InPlace[4] AND (Cell.Phase[2] = INT#70)'))
        self.assertEqual(r.rows[0]['结束标签'],'Cell.Phase[2]=4')
    def test_not_name_based(self):
        r=FeedbackResolver(graph(edge(guard='Cell.Phase[2]=70 AND X.Q'))).resolve(row(end='Cell.Phase[2]=70 AND X.Q'))
        self.assertTrue(r.rows);self.assertFalse(r.paths[0]['feedback_relations'][0]['physical_feedback_role_proved'])
    def test_synthetic_not_a_plc_signal(self):
        r=FeedbackResolver(graph(edge())).resolve(row(end='Cell.Phase[2]=70 AND __source_predicate_x'))
        self.assertFalse(r.applicable)
    def test_random_names_and_values(self):
        rng=random.Random(913)
        for i in range(25):
            v=f'Task{i}.Stage[{rng.randrange(20)}]';f=f'Device{i}.Flag[{rng.randrange(5)}]'
            a,b=rng.sample(range(1000),2)
            g=graph(edge(a,b,variable=v,guard=f'{v}={a} AND {f}'))
            r=FeedbackResolver(g).resolve(row(f'{v}={a}',f'{v}={a} AND {f}'))
            self.assertEqual(r.rows[0]['结束标签'],f'{v}={b}')
    def test_atomic_evaluator_conditional_with_existing_pair_only(self):
        pred=[row(end='Cell.Phase[2]=4')];before=copy.deepcopy(pred)
        rr,_,m,_=evaluate(pred,[row()],graph(edge()))
        self.assertEqual(rr[0]['最佳匹配'],'有条件的迁移替代');self.assertEqual(m['unique_scored_gt'],0)
        self.assertEqual(rr[0]['参考结束标签'],'Cell.Phase[2]=4');self.assertEqual(pred,before)
    def test_missing_prediction_cannot_be_fabricated(self):
        rr,_,m,_=evaluate([row(end='Cell.Phase[2]=9')],[row()],graph(edge()))
        self.assertNotEqual(rr[0]['最佳匹配'],'有条件的迁移替代');self.assertEqual(m['unique_scored_gt'],0)
    def test_no_cross_row_splicing(self):
        rr,_,m,_=evaluate([row(end='Cell.Phase[2]=2'),row('Cell.Phase[2]=2','Cell.Phase[2]=4')],[row()],graph(edge()))
        self.assertEqual(m['unique_scored_gt'],0);self.assertNotEqual(rr[0]['最佳匹配'],'有条件的迁移替代')
    def test_full_pool_eval_has_same_diagnosis(self):
        pred=[row(end='Cell.Phase[2]=4')];rr,_,m,_,_=evaluate_full(pred,[row()],graph(edge()))
        self.assertEqual(rr[0]['最佳匹配'],'有条件的迁移替代');self.assertEqual(m['unique_scored_gt'],0)
    def test_true_exact_pair_still_scores(self):
        rr,_,m,_,_=evaluate_full([row()],[row()],graph(edge()))
        self.assertEqual(m['unique_scored_gt'],1);self.assertEqual(rr[0]['最佳匹配'],'严格起止命中')
    def test_export_keeps_lineage_and_references(self):
        raw=[row(),row(end='Cell.Phase[2]=4')];before=copy.deepcopy(raw)
        with tempfile.TemporaryDirectory() as d:
            stats=export_atomic(raw,graph(edge()),d)
            rr,_=read_csv(Path(d)/'prediction_atomic_pairs.csv')
            audit,_=read_csv(Path(d)/'prediction_feedback_successor_audit.csv')
            self.assertEqual(len(rr),1);self.assertEqual(stats['rows_with_atomic_output'],2)
            self.assertTrue(all(atomic(x['开始标签']) and atomic(x['结束标签']) for x in rr))
            self.assertEqual(len(audit),1);self.assertEqual(raw,before)
    def test_resolver_no_gt_parameter(self):
        self.assertEqual(list(inspect.signature(FeedbackResolver.__init__).parameters),['self','graph'])
    def test_plcopen_ld_source_to_feedback(self):
        from test_v9_7 import scan_text, ld_xml
        g=scan_text(ld_xml(target='7'))
        rr=row('Cell.Phase[4]=500','Cell.Phase[4]=500 AND Axis.Arrived')
        r=FeedbackResolver(g).resolve(rr)
        self.assertEqual(r.rows[0]['结束标签'],'Cell.Phase[4]=7')
    def test_st_source_to_feedback_preserves_gate(self):
        from test_v9_7 import scan_text, st
        g=scan_text(st('IF Cell.Phase[2]=70 AND Drive.InPlace[4] AND Run THEN Cell.Phase[2]:=4; END_IF;'))
        r=FeedbackResolver(g).resolve(row())
        self.assertEqual(r.rows[0]['结束标签'],'Cell.Phase[2]=4');self.assertIn('Run',r.rows[0]['附加门控'])
    def test_skill_and_code_have_no_project_whitelist(self):
        root=Path(__file__).parent
        for name in ('feedback_successor.py','.opencode/skills/plc-feedback-successor/SKILL.md'):
            text=(root/name).read_text(encoding='utf-8')
            self.assertNotIn('ST09.',text);self.assertNotIn('SV44.',text);self.assertNotIn('m_nNextAutoStep',text)
        self.assertIn('plc-feedback-successor',(root/'AGENTS.md').read_text(encoding='utf-8'))
    def test_untrusted_graph_rejected(self):
        with self.assertRaises(ValueError):FeedbackResolver({'gt':[row()]})


if __name__=='__main__':unittest.main()
