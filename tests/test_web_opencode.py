import json
import os
import tempfile
import unittest
from pathlib import Path

from web_opencode import (
    BatchSpec,
    build_opencode_command,
    load_batch_specs,
    result_is_valid,
    inspect_opencode,
)


class WebOpenCodeTests(unittest.TestCase):
    def make_run(self, root: Path):
        run = root / "sample"
        (run / "batches").mkdir(parents=True)
        (run / "batches_desc").mkdir()
        manifest = {
            "batches": [{
                "batch_id": "B00001",
                "input": "batches/B00001.input.json",
                "output": "batches/B00001.result.json",
            }]
        }
        (run / "workflow_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        process_input = {
            "batch_id": "B00001",
            "candidates": [{"候选ID": "C0000001", "开始标签": "Step=10", "结束标签": "Step=20"}],
            "events": [{"事件ID": "E0000001", "guard": "Step=10", "next_state": "Step=20"}],
        }
        (run / "batches/B00001.input.json").write_text(json.dumps(process_input), encoding="utf-8")
        desc_input = {
            "batch_id": 1,
            "rows": [{"id": "0", "kind": "步进", "tokens": [
                {"tok": "EM01.Axis_Z1", "word": "Z轴", "action": "伺服定位"},
                {"tok": "EM01.Alarm", "word": "报警", "action": "报警"}
            ]}],
            "rules": {"max_len": 25},
            "task": "动作描述",
        }
        (run / "batches_desc/batch_001.input.json").write_text(json.dumps(desc_input), encoding="utf-8")
        return run

    def test_load_batch_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            specs = load_batch_specs(run)
            self.assertEqual([s.kind for s in specs], ["process", "action"])
            self.assertEqual(specs[0].batch_id, "B00001")
            self.assertEqual(specs[1].batch_id, "1")
            self.assertEqual(specs[1].output_path.name, "batch_001.result.json")

    def test_process_result_event_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            spec = load_batch_specs(run)[0]
            spec.output_path.write_text(json.dumps({
                "batch_id": "B00001",
                "steps": [{
                    "开始标签": "Step=10",
                    "结束标签": "Step=20",
                    "证据候选ID": ["C0000001"],
                    "证据事件ID": ["E0000001"],
                }],
                "unresolved_events": [],
                "notes": "",
            }), encoding="utf-8")
            self.assertEqual(result_is_valid(spec)[0], True)

            broken = json.loads(spec.output_path.read_text(encoding="utf-8"))
            broken["steps"][0]["证据事件ID"] = []
            spec.output_path.write_text(json.dumps(broken), encoding="utf-8")
            valid, reason = result_is_valid(spec)
            self.assertFalse(valid)
            self.assertIn("没有进入", reason)

            broken["steps"][0]["证据事件ID"] = ["E0000001"]
            # Normalized labels treat all whitespace as insignificant.
            broken["steps"][0]["开始标签"] = "Step = 10"
            spec.output_path.write_text(json.dumps(broken), encoding="utf-8")
            self.assertTrue(result_is_valid(spec)[0])

            broken["steps"][0]["开始标签"] = "Step=999"
            spec.output_path.write_text(json.dumps(broken), encoding="utf-8")
            valid, reason = result_is_valid(spec)
            self.assertFalse(valid)
            self.assertIn("开始标签无法", reason)

    def test_manifest_rejects_paths_outside_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            manifest = json.loads((run / "workflow_manifest.json").read_text(encoding="utf-8"))
            manifest["batches"][0]["input"] = "../../outside.json"
            (run / "workflow_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_batch_specs(run)

    def test_action_result_covers_every_row_and_only_uses_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            spec = load_batch_specs(run)[1]
            spec.output_path.write_text(json.dumps({
                "batch_id": 1,
                "descs": {
                    "0": {
                        "ok": True,
                        "desc": "Z轴伺服定位",
                        "refs": ["EM01.Axis_Z1"],
                        "kind": "步进",
                    }
                },
            }), encoding="utf-8")
            self.assertTrue(result_is_valid(spec)[0])

            broken = json.loads(spec.output_path.read_text(encoding="utf-8"))
            broken["descs"]["0"]["refs"] = ["Invented.Axis"]
            spec.output_path.write_text(json.dumps(broken), encoding="utf-8")
            self.assertFalse(result_is_valid(spec)[0])

            # Original v9.25 validation allows a focused subset of all tokens.
            broken["descs"]["0"]["refs"] = ["EM01.Axis_Z1"]
            spec.output_path.write_text(json.dumps(broken), encoding="utf-8")
            self.assertTrue(result_is_valid(spec)[0])

            # Missing B-tier rows must not reject the batch: finish uses A fallback.
            spec.output_path.write_text(json.dumps({
                "batch_id": 1, "descs": {}
            }), encoding="utf-8")
            valid, message = result_is_valid(spec)
            self.assertTrue(valid)
            self.assertIn("兜底", message)

            broken["descs"]["unexpected"] = {"ok": True, "desc": "额外行", "refs": [], "kind": "步进"}
            spec.output_path.write_text(json.dumps(broken), encoding="utf-8")
            self.assertFalse(result_is_valid(spec)[0])

    @unittest.skipIf(os.name == "nt", "POSIX shell fixture only")
    def test_inspect_opencode_detects_cli_and_required_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "fake-opencode"
            fake.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"--version\" ]; then echo 'opencode 1.2.3'; exit 0; fi\n"
                "if [ \"$1\" = \"agent\" ] && [ \"$2\" = \"list\" ]; then "
                "echo 'plc-sequence'; exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            previous = os.environ.get("OPENCODE_BIN")
            os.environ["OPENCODE_BIN"] = str(fake)
            try:
                status = inspect_opencode(root)
            finally:
                if previous is None:
                    os.environ.pop("OPENCODE_BIN", None)
                else:
                    os.environ["OPENCODE_BIN"] = previous
            self.assertTrue(status["available"])
            self.assertTrue(status["agents_ready"])
            self.assertEqual(set(status["agents"]), {"plc-sequence"})

    def test_native_web_uses_original_agent_and_four_fields(self):
        from web_app import _build_native_command
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "outputs" / "job1"
            run.mkdir(parents=True)
            (run / "web_request.json").write_text(json.dumps({
                "source": "C:/PLC/source.xml",
                "pattern": "ST*.AutoStep[*]",
                "trace": "C:/PLC/runtime.trace",
                "manual": "C:/PLC/manual.xlsx",
                "out": str(run),
            }), encoding="utf-8")
            command = _build_native_command("opencode", run, False)
            self.assertEqual(command[:4], ["opencode", "run", "--agent", "plc-sequence"])
            self.assertNotIn("--file", command)
            self.assertNotIn("plc-web-orchestrator", command)
            prompt = command[-1]
            self.assertTrue(prompt.startswith(
                "C:/PLC/source.xml ST*.AutoStep[*] C:/PLC/runtime.trace C:/PLC/manual.xlsx"))
            self.assertIn(str(run), prompt)
            self.assertNotIn("web_workflow_runner.py", prompt)

    def test_native_web_handles_omitted_optional_inputs(self):
        from web_app import _build_native_command
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "outputs" / "job2"
            run.mkdir(parents=True)
            (run / "web_request.json").write_text(json.dumps({
                "source": "C:/PLC/file with spaces.xml",
                "pattern": "ST*.AutoStep[*]",
                "trace": "", "manual": "", "out": str(run),
            }), encoding="utf-8")
            command = _build_native_command("opencode", run, False)
            self.assertTrue(command[-1].startswith(
                '"C:/PLC/file with spaces.xml" ST*.AutoStep[*]'))
            self.assertNotIn("plc-web-orchestrator", command[-1])

    def test_opencode_command_is_noninteractive_and_batch_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            spec = load_batch_specs(run)[0]
            command = build_opencode_command("opencode", spec, Path(tmp))
            self.assertEqual(command[0:2], ["opencode", "run"])
            self.assertIn("--agent", command)
            self.assertIn("plc-process-batch", command)
            self.assertIn("--auto", command)
            self.assertIn("--file", command)
            self.assertIn(str(spec.input_path), command)
            self.assertIn("sample/batches/B00001.result.json", command[-1])


if __name__ == "__main__":
    unittest.main()
