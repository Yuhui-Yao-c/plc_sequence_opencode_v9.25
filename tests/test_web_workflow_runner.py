import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_workflow_runner as runner
from web_opencode import load_batch_specs


class WorkflowRunnerTests(unittest.TestCase):
    def make_environment(self, root):
        outputs = root / 'outputs'
        run = outputs / 'sample'
        run.mkdir(parents=True)
        (root / 'sample.xml').write_text('<project/>', encoding='utf-8')
        (run / 'web_request.json').write_text(json.dumps({
            'source': str(root / 'sample.xml'),
            'pattern': '*',
            'out': str(run),
            'manual': '', 'trace': '', 'python': 'python'
        }), encoding='utf-8')
        (run / 'batches').mkdir()
        (run / 'batches_desc').mkdir()
        return run

    def install_manifest(self, run):
        (run / 'workflow_manifest.json').write_text(json.dumps({
            'batches': [{'batch_id': 'B00001',
                         'input': 'batches/B00001.input.json',
                         'output': 'batches/B00001.result.json'}]
        }), encoding='utf-8')
        (run / 'batches/B00001.input.json').write_text(json.dumps({
            'batch_id': 'B00001', 'candidates': [],
            'events': [{'事件ID':'E0000001'}]
        }), encoding='utf-8')
        (run / 'action_desc_a.csv').write_text('行号,动作描述\n', encoding='utf-8')

    def test_stage_guard_and_checkpoint_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = self.make_environment(root)
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'OUTPUTS', root / 'outputs'):
                self.assertTrue(runner.state(run)['need_predict'])
                with self.assertRaises(RuntimeError):
                    runner.finish(run)
                self.install_manifest(run)
                waiting = runner.state(run)
                self.assertEqual(waiting['pending_count'], 1)
                self.assertEqual(waiting['next_pending']['agent'], 'plc-process-batch')
                with self.assertRaises(RuntimeError):
                    runner.finish(run)
                (run / 'batches/B00001.result.json').write_text(json.dumps({
                    'batch_id': 'B00001', 'steps': [],
                    'unresolved_events':[{'event_id':'E0000001','reason':'证据不足'}],
                    'notes':'不凭空生成机械动作'
                }), encoding='utf-8')
                self.assertTrue(runner.state(run)['ready_for_finish'])
                def simulate_finish(command):
                    self.assertEqual(command[2], 'finish')
                    (run / 'workflow_status.json').write_text('{"success":true}',encoding='utf-8')
                    (run / 'sample_步序采集表.xlsx').write_bytes(b'PK')
                    return 0
                with patch.object(runner, 'execute', side_effect=simulate_finish):
                    self.assertEqual(runner.finish(run), 0)
                self.assertTrue(runner.state(run)['finished'])

    def test_predict_only_runs_via_primitive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = self.make_environment(root)
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'OUTPUTS', root / 'outputs'):
                def simulate_predict(command):
                    self.assertEqual(command[2], 'predict')
                    self.assertIn('--source', command)
                    self.assertIn('--out', command)
                    self.install_manifest(run)
                    return 0
                with patch.object(runner, 'execute', side_effect=simulate_predict) as spy:
                    self.assertEqual(runner.predict(run), 0)
                    self.assertEqual(runner.predict(run), 0)
                    self.assertEqual(spy.call_count, 1)

    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(runner, 'OUTPUTS', Path(temp)):
                for name in ('../outside', '.', '..', 'not/a/run'):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        runner.run_directory(name)


if __name__ == '__main__':
    unittest.main()
