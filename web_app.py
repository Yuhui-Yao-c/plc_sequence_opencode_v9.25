#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PLC 步序采集工作流 — Web 界面后端 (Flask)

启动: python web_app.py
访问: http://127.0.0.1:5173
"""
import os
import sys
import json
import csv
import glob
import time
import uuid
import threading
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify, send_file, Response

PROJECT_ROOT = Path(__file__).parent.resolve()
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
WEB_DIR = PROJECT_ROOT / "web"

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")

# ---------------------------------------------------------------------------
# Job management
# ---------------------------------------------------------------------------
_jobs = {}
_jobs_lock = threading.Lock()


def _run_command(job_id, cmd, cwd):
    """在后台线程中运行命令，逐行捕获输出。"""
    job = _jobs[job_id]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(cwd),
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        job["pid"] = proc.pid
        for line in iter(proc.stdout.readline, ""):
            with _jobs_lock:
                job["output"].append(line.rstrip("\n\r"))
                job["last_update"] = time.time()
        proc.wait()
        with _jobs_lock:
            job["status"] = "completed" if proc.returncode == 0 else "failed"
            job["returncode"] = proc.returncode
            job["end_time"] = time.time()
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["end_time"] = time.time()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_csv_row_count(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            return max(sum(1 for _ in reader) - 1, 0)
    except Exception:
        return 0


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _run_dir(name):
    return OUTPUTS_DIR / name


def _batch_status(run_dir):
    """检查工艺批次和 B 档批次的完成情况。"""
    # 工艺批次: batches/B*.input.json / B*.result.json
    batches_dir = run_dir / "batches"
    proc_inputs = list(batches_dir.glob("*.input.json")) if batches_dir.exists() else []
    proc_results = list(batches_dir.glob("*.result.json")) if batches_dir.exists() else []

    # B 档批次: batches_desc/batch_*.input.json / batch_*.result.json
    desc_dir = run_dir / "batches_desc"
    desc_inputs = list(desc_dir.glob("batch_*.input.json")) if desc_dir.exists() else []
    desc_results = list(desc_dir.glob("batch_*.result.json")) if desc_dir.exists() else []

    return {
        "process": {
            "total": len(proc_inputs),
            "completed": len(proc_results),
            "pending": len(proc_inputs) - len(proc_results),
        },
        "b_tier": {
            "total": len(desc_inputs),
            "completed": len(desc_results),
            "pending": len(desc_inputs) - len(desc_results),
        },
    }


def _run_stats(run_dir):
    """汇总一个 run 目录的统计数据。"""
    stats = {}
    # summary json
    for name in ["prediction_collection_summary", "prediction_atomic_summary"]:
        d = _read_json(run_dir / f"{name}.json")
        if d:
            stats[name] = d

    # CSV counts
    stats["candidate_pairs"] = _read_csv_row_count(run_dir / "prediction_candidate_pairs.csv")
    stats["collection_pairs"] = _read_csv_row_count(run_dir / "prediction_collection_pairs.csv")
    stats["atomic_pairs"] = _read_csv_row_count(run_dir / "prediction_atomic_pairs.csv")
    stats["a_desc_rows"] = _read_csv_row_count(run_dir / "action_desc_a.csv")
    stats["review_rows"] = _read_csv_row_count(run_dir / "prediction_atomic_review.csv")

    # batch status
    stats["batches"] = _batch_status(run_dir)

    # workflow_status (finish 后存在)
    ws = _read_json(run_dir / "workflow_status.json")
    if ws:
        stats["workflow_status"] = ws
        stats["finished"] = ws.get("success", False)
    else:
        stats["finished"] = False

    # workflow_manifest (predict 后存在)
    wm = _read_json(run_dir / "workflow_manifest.json")
    if wm:
        stats["manifest"] = {
            "source": wm.get("source", ""),
            "trace": wm.get("trace", ""),
            "manual": wm.get("manual", ""),
            "version": wm.get("version", ""),
        }

    # 交付表
    xlsx_files = list(run_dir.glob("*_步序采集表.xlsx"))
    stats["delivery_xlsx"] = xlsx_files[0].name if xlsx_files else None

    # 覆盖评价
    eval_dir = run_dir / "评价"
    if eval_dir.exists():
        eval_files = list(eval_dir.glob("*_覆盖评价.xlsx"))
        stats["coverage_xlsx"] = eval_files[0].name if eval_files else None
    else:
        stats["coverage_xlsx"] = None

    return stats


def _list_run_files(run_dir):
    """列出 run 目录下的关键交付文件。"""
    key_files = [
        "prediction_collection_pairs.csv",
        "prediction_candidate_pairs.csv",
        "prediction_atomic_pairs.csv",
        "final_prediction_pairs.csv",
        "final_prediction_pairs_full.csv",
        "final_sequence.csv",
        "final_process_steps.csv",
        "action_desc_a.csv",
        "action_desc_final.csv",
        "action_desc_review.csv",
        "prediction_atomic_review.csv",
        "prediction_atomic_lineage.csv",
        "prediction_atomic_conditions.json",
        "workflow_manifest.json",
        "workflow_status.json",
        "variable_documentation.json",
        "step_comments.json",
        "station_identity.json",
    ]
    result = []
    for name in key_files:
        p = run_dir / name
        if p.exists():
            result.append({"name": name, "size": p.stat().st_size})
    # 交付表
    for xlsx in run_dir.glob("*_步序采集表.xlsx"):
        result.append({"name": xlsx.name, "size": xlsx.stat().st_size})
    # 覆盖评价
    eval_dir = run_dir / "评价"
    if eval_dir.exists():
        for xlsx in eval_dir.glob("*.xlsx"):
            result.append({"name": f"评价/{xlsx.name}", "size": xlsx.stat().st_size})
    # trace 分析
    trace_dir = run_dir / "trace_analysis"
    if trace_dir.exists():
        for f in trace_dir.iterdir():
            if f.is_file():
                result.append({"name": f"trace_analysis/{f.name}", "size": f.stat().st_size})
    return result


# ---------------------------------------------------------------------------
# Routes — Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return app.send_static_file("index.html")


# ---------------------------------------------------------------------------
# Routes — File browse
# ---------------------------------------------------------------------------
@app.route("/api/browse", methods=["POST"])
def api_browse():
    data = request.json or {}
    mode = data.get("mode", "file")
    file_filter = data.get("filter", "")
    title = data.get("title", "选择文件")

    helper = WEB_DIR / "_browse.py"
    cmd = [sys.executable, str(helper), mode, file_filter, title]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        path = result.stdout.strip()
        return jsonify({"path": path})
    except subprocess.TimeoutExpired:
        return jsonify({"path": "", "error": "选择超时"}), 500
    except Exception as exc:
        return jsonify({"path": "", "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Routes — Predict / Finish
# ---------------------------------------------------------------------------
@app.route("/api/predict", methods=["POST"])
def api_predict():
    data = request.json or {}
    source = (data.get("source") or "").strip()
    pattern = (data.get("pattern") or "*").strip()
    out = (data.get("out") or "").strip()
    trace = (data.get("trace") or "").strip()
    manual = (data.get("manual") or "").strip()
    force = data.get("force", False)

    if not source:
        return jsonify({"error": "请填写源码 XML 路径"}), 400
    if not out:
        return jsonify({"error": "请填写输出目录"}), 400

    cmd = [
        sys.executable, "plc_cli.py", "predict",
        "--source", source,
        "--pattern", pattern,
        "--out", out,
    ]
    if trace:
        cmd += ["--trace", trace]
    if manual:
        cmd += ["--manual", manual]
    if force:
        cmd += ["--force"]

    run_name = Path(out).name
    job_id = uuid.uuid4().hex[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "type": "predict",
            "status": "running",
            "output": [],
            "start_time": time.time(),
            "run_name": run_name,
            "command": " ".join(cmd),
            "lines_sent": 0,
        }

    t = threading.Thread(target=_run_command, args=(job_id, cmd, PROJECT_ROOT), daemon=True)
    t.start()
    return jsonify({"job_id": job_id, "run_name": run_name})


@app.route("/api/finish", methods=["POST"])
def api_finish():
    data = request.json or {}
    run_name = (data.get("run_name") or "").strip()
    if not run_name:
        return jsonify({"error": "缺少 run_name"}), 400

    run_path = OUTPUTS_DIR / run_name
    if not run_path.exists():
        return jsonify({"error": f"运行目录不存在: {run_name}"}), 404

    cmd = [sys.executable, "plc_cli.py", "finish", "--run", run_name]
    job_id = uuid.uuid4().hex[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "type": "finish",
            "status": "running",
            "output": [],
            "start_time": time.time(),
            "run_name": run_name,
            "command": " ".join(cmd),
            "lines_sent": 0,
        }

    t = threading.Thread(target=_run_command, args=(job_id, cmd, PROJECT_ROOT), daemon=True)
    t.start()
    return jsonify({"job_id": job_id, "run_name": run_name})


# ---------------------------------------------------------------------------
# Routes — Job polling
# ---------------------------------------------------------------------------
@app.route("/api/job/<job_id>", methods=["GET"])
def api_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        sent = job.get("lines_sent", 0)
        new_lines = job["output"][sent:]
        job["lines_sent"] = len(job["output"])
        elapsed = 0
        if job["status"] == "running":
            elapsed = time.time() - job["start_time"]
        else:
            elapsed = job.get("end_time", job["start_time"]) - job["start_time"]
        return jsonify({
            "id": job_id,
            "type": job["type"],
            "status": job["status"],
            "new_lines": new_lines,
            "total_lines": len(job["output"]),
            "returncode": job.get("returncode"),
            "run_name": job.get("run_name"),
            "elapsed": round(elapsed, 1),
            "error": job.get("error"),
        })


# ---------------------------------------------------------------------------
# Routes — Runs
# ---------------------------------------------------------------------------
@app.route("/api/runs", methods=["GET"])
def api_runs():
    runs = []
    if OUTPUTS_DIR.exists():
        for d in sorted(OUTPUTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if d.is_dir() and not d.name.startswith("."):
                wm = _read_json(d / "workflow_manifest.json")
                ws = _read_json(d / "workflow_status.json")
                runs.append({
                    "name": d.name,
                    "mtime": d.stat().st_mtime,
                    "source": (wm or {}).get("source", ""),
                    "finished": bool(ws and ws.get("success")),
                    "has_manifest": wm is not None,
                })
    return jsonify(runs)


@app.route("/api/run/<name>/info", methods=["GET"])
def api_run_info(name):
    rd = _run_dir(name)
    if not rd.exists():
        return jsonify({"error": "运行目录不存在"}), 404
    return jsonify(_run_stats(rd))


@app.route("/api/run/<name>/pairs", methods=["GET"])
def api_run_pairs(name):
    rd = _run_dir(name)
    if not rd.exists():
        return jsonify({"error": "运行目录不存在"}), 404

    # 优先 final_prediction_pairs.csv (finish 后)，否则 prediction_collection_pairs.csv
    csv_path = rd / "final_prediction_pairs.csv"
    if not csv_path.exists():
        csv_path = rd / "prediction_collection_pairs.csv"
    if not csv_path.exists():
        return jsonify({"columns": [], "rows": [], "total": 0})

    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 100))
    search = (request.args.get("search") or "").lower()

    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            columns = list(reader.fieldnames or [])
            all_rows = list(reader)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    # 合并动作描述：优先 action_desc_final.csv，其次 action_desc_a.csv
    desc_map = {}
    for desc_file in ["action_desc_final.csv", "action_desc_a.csv"]:
        dp = rd / desc_file
        if dp.exists():
            try:
                with open(dp, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for idx, drow in enumerate(reader):
                        desc = (drow.get("动作描述") or drow.get("A档描述") or "").strip()
                        if desc:
                            desc_map[idx] = desc
                break  # 优先 final，找到就不再读 a
            except Exception:
                pass
    if desc_map:
        for idx, row in enumerate(all_rows):
            if idx in desc_map:
                row["动作描述"] = desc_map[idx]
        if "动作描述" not in columns:
            columns.insert(4, "动作描述")

    # 搜索过滤
    if search:
        filtered = []
        for row in all_rows:
            blob = " ".join(str(v) for v in row.values()).lower()
            if search in blob:
                filtered.append(row)
        all_rows = filtered

    total = len(all_rows)
    start = (page - 1) * per_page
    end = start + per_page
    page_rows = all_rows[start:end]

    return jsonify({
        "columns": columns,
        "rows": page_rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    })


@app.route("/api/run/<name>/batches", methods=["GET"])
def api_run_batches(name):
    rd = _run_dir(name)
    if not rd.exists():
        return jsonify({"error": "运行目录不存在"}), 404

    result = {"process": [], "b_tier": []}

    # 工艺批次
    batches_dir = rd / "batches"
    if batches_dir.exists():
        for inp in sorted(batches_dir.glob("*.input.json")):
            bid = inp.stem.replace(".input", "")
            res_path = batches_dir / f"{bid}.result.json"
            result["process"].append({
                "batch_id": bid,
                "has_input": True,
                "has_result": res_path.exists(),
            })

    # B 档批次
    desc_dir = rd / "batches_desc"
    if desc_dir.exists():
        for inp in sorted(desc_dir.glob("batch_*.input.json")):
            bid = inp.stem.replace(".input", "")
            res_path = desc_dir / f"{bid}.result.json"
            result["b_tier"].append({
                "batch_id": bid,
                "has_input": True,
                "has_result": res_path.exists(),
            })

    return jsonify(result)


@app.route("/api/run/<name>/batch/<batch_type>/<batch_id>", methods=["GET"])
def api_run_batch_detail(name, batch_type, batch_id):
    rd = _run_dir(name)
    if not rd.exists():
        return jsonify({"error": "运行目录不存在"}), 404

    if batch_type == "process":
        inp_path = rd / "batches" / f"{batch_id}.input.json"
        res_path = rd / "batches" / f"{batch_id}.result.json"
    else:
        inp_path = rd / "batches_desc" / f"{batch_id}.input.json"
        res_path = rd / "batches_desc" / f"{batch_id}.result.json"

    data = {"batch_id": batch_id, "input": None, "result": None}
    if inp_path.exists():
        data["input"] = _read_json(inp_path)
    if res_path.exists():
        data["result"] = _read_json(res_path)
    return jsonify(data)


@app.route("/api/run/<name>/files", methods=["GET"])
def api_run_files(name):
    rd = _run_dir(name)
    if not rd.exists():
        return jsonify({"error": "运行目录不存在"}), 404
    return jsonify(_list_run_files(rd))


@app.route("/api/run/<name>/download", methods=["GET"])
def api_run_download(name):
    rd = _run_dir(name)
    if not rd.exists():
        return jsonify({"error": "运行目录不存在"}), 404
    filename = request.args.get("file", "")
    if not filename:
        return jsonify({"error": "缺少 file 参数"}), 400

    # 防止路径穿越
    safe = filename.replace("\\", "/").replace("..", "")
    file_path = rd / safe
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "文件不存在"}), 404

    return send_file(str(file_path), as_attachment=True, download_name=file_path.name)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = 5173
    print(f"\n  PLC 步序采集工作流 Web 界面")
    print(f"  访问: http://127.0.0.1:{port}\n")
    try:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}")
    except Exception:
        pass
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
