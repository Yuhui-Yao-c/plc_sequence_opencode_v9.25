"""Synthetic web integration fixture; never used for PLC prediction."""
import argparse
import csv
import json
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("--source")
parser.add_argument("--pattern")
parser.add_argument("--out")
parser.add_argument("--run")
parser.add_argument("--manual")
parser.add_argument("--trace")
args = parser.parse_args()
run = Path(args.out or args.run)
mode_file = Path(__file__).parent / "fixture-mode.txt"
mode = mode_file.read_text().strip() if mode_file.exists() else "normal"


def write(name, data):
    target = run / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def table(name, rows):
    with (run / name).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerows(rows)


if args.command == "predict":
    if mode == "predict-fail":
        raise SystemExit("fixture parse failure")
    assert not (run / "workflow_manifest.json").exists(), "must not overwrite a run"
    write("workflow_manifest.json", {
        "source": args.source, "manual": args.manual, "trace": args.trace,
        "filter": {"pattern": args.pattern}, "candidate_count": 2,
        "batches": [{"batch_id": "B00001", "input": "batches/B00001.input.json", "output": "batches/B00001.result.json"}],
    })
    write("batches/B00001.input.json", {"batch_id": "B00001", "events": [], "candidates": []})
    write("batches_desc/batch_001.input.json", {"rows": [{"id": "1"}, {"id": "2"}]})
    table("prediction_collection_pairs.csv", [
        ["构成要素", "动作描述", "动作变量", "开始标签", "结束标签", "备注"],
        ["工位'一", "", "A.Cyl", "Cell.Phase=10", "Cell.Phase=20", "第一行\n含换行,和逗号"],
        ["工位二", "", "B.Cyl", "Cell.Phase=10", "Cell.Phase=20", "第二行"],
    ])
    print("fixture: 源码解析完成", flush=True)
elif args.command == "finish":
    if mode == "finish-fail":
        raise SystemExit("fixture finish failure")
    if mode == "semantic-fail":
        write("workflow_status.json", {"success": False, "invalid_issues": ["B00001 invalid evidence"]})
        raise SystemExit("fixture semantic validation failure")
    assert (run / "batches/B00001.result.json").exists()
    assert (run / "batches_desc/batch_001.result.json").exists()
    (run / "final_prediction_pairs.csv").write_bytes((run / "prediction_collection_pairs.csv").read_bytes())
    table("action_desc_final.csv", [
        ["行号", "开始标签", "结束标签", "动作描述", "动作变量", "描述来源"],
        ["1", "Cell.Phase=10", "Cell.Phase=20", "夹紧气缸", "A.Cyl", "测试夹具"],
        ["2", "Cell.Phase=10", "Cell.Phase=20", "顶升气缸", "B.Cyl", "测试夹具"],
    ])
    if mode != "workbook-fail":
        (run / "测试_步序采集表.xlsx").write_bytes(b"fixture-workbook-bytes")
    status = {"success": True, "final_rows": 2}
    if mode == "workbook-fail":
        status["workstation_xlsx"] = {"error": "fixture workbook failure"}
    if args.command == "finish":
        manifest = json.loads((run / "workflow_manifest.json").read_text())
        if manifest.get("manual"):
            (run / "评价").mkdir(exist_ok=True)
            (run / "评价/测试_覆盖评价.xlsx").write_bytes(b"fixture-coverage")
            status["coverage"] = {"proc_name": "测试"}
    write("workflow_status.json", status)
    print("fixture: 校验交付完成", flush=True)
