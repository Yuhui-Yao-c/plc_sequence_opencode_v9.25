#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PLC Sequence Agent v9.25 — 本地 Web 控制台。

浏览器只调用 Flask API；Flask 在本机启动 Python CLI 与 OpenCode CLI。
默认仅监听 127.0.0.1，避免把本地工程文件和执行接口暴露到局域网。
"""
from __future__ import annotations

import csv
import json
import os
import platform
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from importlib import metadata
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from web_opencode import (
    build_opencode_command,
    inspect_opencode,
    load_batch_specs,
    result_is_valid,
    timeout_seconds,
)

PROJECT_ROOT = Path(__file__).parent.resolve()
OUTPUTS_DIR = (PROJECT_ROOT / "outputs").resolve()
WEB_DIR = PROJECT_ROOT / "web"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")
app.config["JSON_AS_ASCII"] = False
try:
    app.json.ensure_ascii = False
except Exception:
    pass

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


class JobCancelled(Exception):
    pass


def _now() -> float:
    return time.time()


def _append_log(job_id: str, line: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job["output"].append(str(line).rstrip("\r\n"))
        job["last_update"] = _now()


def _set_stage(job_id: str, stage: str, current_item: str = "") -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job["stage"] = stage
            job["current_item"] = current_item
            job["last_update"] = _now()


def _set_progress(job_id: str, done: int, total: int) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job["progress"] = {
                "done": max(0, int(done)),
                "total": max(0, int(total)),
                "percent": round(done / total * 100, 1) if total else 0,
            }
            job["last_update"] = _now()


def _create_job(job_type: str, run_name: str) -> str:
    job_id = uuid.uuid4().hex[:10]
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "type": job_type,
            "status": "running",
            "stage": "排队中",
            "current_item": "",
            "progress": {"done": 0, "total": 0, "percent": 0},
            "output": [],
            "lines_sent": 0,
            "start_time": _now(),
            "last_update": _now(),
            "run_name": run_name,
            "returncode": None,
            "error": None,
            "process": None,
            "cancel_requested": False,
        }
    return job_id


def _finish_job(job_id: str, status: str, error: str | None = None, returncode: int | None = None) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job["status"] = status
        job["stage"] = {
            "completed": "全部完成",
            "failed": "执行失败",
            "cancelled": "已取消",
        }.get(status, status)
        job["error"] = error
        job["returncode"] = returncode
        job["end_time"] = _now()
        job["process"] = None
        job["last_update"] = _now()


def _job_cancelled(job_id: str) -> bool:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return bool(job and job.get("cancel_requested"))


def _command_for_popen(command: list[str]) -> tuple[object, bool]:
    """Handle npm's opencode.cmd shim on Windows; use argv everywhere else."""
    if os.name == "nt" and Path(command[0]).suffix.lower() in {".cmd", ".bat"}:
        return subprocess.list2cmdline(command), True
    return command, False


def _run_command(
    job_id: str,
    command: list[str],
    cwd: Path,
    timeout: int | None = None,
) -> int:
    command_text = shlex.join(command)
    _append_log(job_id, f"$ {command_text}")
    popen_command, use_shell = _command_for_popen(command)
    kwargs = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "shell": use_shell,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(popen_command, **kwargs)
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["process"] = proc
            _jobs[job_id]["pid"] = proc.pid

    lines: queue.Queue = queue.Queue()

    def _reader() -> None:
        try:
            if proc.stdout:
                for line in proc.stdout:
                    lines.put(line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    started = time.monotonic()
    eof = False
    terminate_at = None
    timeout_at = None

    while not eof or proc.poll() is None:
        try:
            item = lines.get(timeout=0.15)
            if item is None:
                eof = True
            else:
                _append_log(job_id, item)
        except queue.Empty:
            pass

        now = time.monotonic()
        if _job_cancelled(job_id) and proc.poll() is None:
            if terminate_at is None:
                terminate_at = now
                try:
                    proc.terminate()
                except Exception:
                    pass
            elif now - terminate_at > 4:
                try:
                    proc.kill()
                except Exception:
                    pass

        if timeout and now - started > timeout and proc.poll() is None:
            if timeout_at is None:
                timeout_at = now
                try:
                    proc.terminate()
                except Exception:
                    pass
            elif now - timeout_at > 4:
                try:
                    proc.kill()
                except Exception:
                    pass

    returncode = proc.wait()
    reader.join(timeout=1)
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["process"] = None

    if _job_cancelled(job_id):
        raise JobCancelled("用户取消了当前任务")
    if timeout_at is not None:
        raise TimeoutError(f"命令执行超过 {timeout} 秒，已终止")
    return returncode


def _resolve_run_dir(name: str) -> Path:
    name = (name or "").strip()
    if not name or name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("运行名称无效")
    path = (OUTPUTS_DIR / name).resolve()
    if path.parent != OUTPUTS_DIR:
        raise ValueError("运行目录必须位于 outputs/ 下")
    return path


def _resolve_output_dir(value: str) -> Path:
    value = (value or "").strip()
    if not value:
        raise ValueError("请填写输出目录")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    if path.parent != OUTPUTS_DIR:
        raise ValueError("为保证历史记录与下载正常工作，输出目录必须是项目 outputs/ 下的一级目录")
    if path.name in {"", ".", ".."}:
        raise ValueError("输出目录名称无效")
    if not re.fullmatch(r"[\w\u4e00-\u9fff .()-]{1,100}", path.name, flags=re.UNICODE):
        raise ValueError("输出目录名称只能包含中英文、数字、空格、点、括号、下划线和连字符")
    return path


def _validate_workflow_input(data: dict) -> dict:
    source_text = str(data.get("source") or "").strip()
    if not source_text:
        raise ValueError("请先选择 PLC 源码文件")
    source = Path(source_text).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"源码文件不存在：{source}")
    if source.suffix.lower() not in {".xml", ".smc2", ".txt"}:
        raise ValueError("源码文件格式必须为 .xml、.smc2 或 .txt")

    pattern = str(data.get("pattern") or "*").strip() or "*"
    if len(pattern) > 300:
        raise ValueError("变量匹配模式过长")

    out_dir = _resolve_output_dir(str(data.get("out") or ""))
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"输出目录已有文件：{out_dir}。请使用新的运行名称，避免覆盖历史结果。")

    trace = str(data.get("trace") or "").strip()
    if trace:
        trace_path = Path(trace).expanduser().resolve()
        if not trace_path.is_file():
            raise FileNotFoundError(f"Trace 文件不存在：{trace_path}")
        trace = str(trace_path)

    manual = str(data.get("manual") or "").strip()
    if manual:
        manual_path = Path(manual).expanduser().resolve()
        if not manual_path.is_file():
            raise FileNotFoundError(f"人工采集表不存在：{manual_path}")
        if manual_path.suffix.lower() not in {".xls", ".xlsx"}:
            raise ValueError("人工采集表必须为 .xls 或 .xlsx 文件")
        manual = str(manual_path)

    return {
        "source": str(source),
        "pattern": pattern,
        "out_dir": out_dir,
        "run_name": out_dir.name,
        "trace": trace,
        "manual": manual,
    }


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_csv_row_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return max(sum(1 for _ in csv.reader(f)) - 1, 0)
    except Exception:
        return 0


def _batch_status(run_dir: Path, validate: bool = True) -> dict:
    specs = []
    try:
        specs = load_batch_specs(run_dir)
    except Exception:
        pass
    process = [x for x in specs if x.kind == "process"]
    action = [x for x in specs if x.kind == "action"]
    if validate:
        process_done = sum(1 for x in process if result_is_valid(x)[0])
        action_done = sum(1 for x in action if result_is_valid(x)[0])
    else:
        # History view only needs a cheap estimate; full validation is deferred
        # until a run is opened or resumed.
        process_done = sum(1 for x in process if x.output_path.is_file())
        action_done = sum(1 for x in action if x.output_path.is_file())
    return {
        "process": {"total": len(process), "completed": process_done, "pending": len(process) - process_done},
        "b_tier": {"total": len(action), "completed": action_done, "pending": len(action) - action_done},
    }


def _run_stats(run_dir: Path) -> dict:
    stats: dict = {}
    for name in ("prediction_collection_summary", "prediction_atomic_summary"):
        data = _read_json(run_dir / f"{name}.json")
        if data:
            stats[name] = data

    stats["candidate_pairs"] = _read_csv_row_count(run_dir / "prediction_candidate_pairs.csv")
    stats["collection_pairs"] = _read_csv_row_count(run_dir / "prediction_collection_pairs.csv")
    stats["atomic_pairs"] = _read_csv_row_count(run_dir / "prediction_atomic_pairs.csv")
    stats["a_desc_rows"] = _read_csv_row_count(run_dir / "action_desc_a.csv")
    stats["atomic_review_rows"] = _read_csv_row_count(run_dir / "prediction_atomic_review.csv")
    stats["process_review_rows"] = _read_csv_row_count(run_dir / "process_review_queue.csv")
    stats["action_desc_review_rows"] = _read_csv_row_count(run_dir / "action_desc_review.csv")
    stats["review_rows"] = stats["atomic_review_rows"] + stats["process_review_rows"] + stats["action_desc_review_rows"]
    stats["batches"] = _batch_status(run_dir)

    workflow_status = _read_json(run_dir / "workflow_status.json")
    if workflow_status:
        stats["workflow_status"] = workflow_status
        stats["workflow_validated"] = bool(workflow_status.get("success"))
    else:
        stats["workflow_validated"] = False

    manifest = _read_json(run_dir / "workflow_manifest.json")
    if manifest:
        stats["manifest"] = {
            "source": manifest.get("source", ""),
            "trace": manifest.get("trace", ""),
            "manual": manifest.get("manual", ""),
            "version": manifest.get("version", ""),
            "candidate_count": manifest.get("candidate_count", 0),
            "event_count": manifest.get("event_count", 0),
        }

    xlsx_files = list(run_dir.glob("*_步序采集表.xlsx"))
    stats["delivery_xlsx"] = xlsx_files[0].name if xlsx_files else None
    stats["finished"] = bool(stats.get("workflow_validated") and stats["delivery_xlsx"])
    if workflow_status and isinstance(workflow_status.get("workstation_xlsx"), dict):
        stats["delivery_error"] = workflow_status["workstation_xlsx"].get("error")
    eval_dir = run_dir / "评价"
    eval_files = list(eval_dir.glob("*_覆盖评价.xlsx")) if eval_dir.exists() else []
    stats["coverage_xlsx"] = eval_files[0].name if eval_files else None
    return stats


def _list_run_files(run_dir: Path) -> list[dict]:
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
        "source_transition_graph.json",
        "unresolved_events.csv",
        "process_review_queue.csv",
    ]
    result = []
    for name in key_files:
        path = run_dir / name
        if path.is_file():
            result.append({"name": name, "size": path.stat().st_size})
    for path in sorted(run_dir.glob("*_步序采集表.xlsx")):
        result.append({"name": path.name, "size": path.stat().st_size})
    eval_dir = run_dir / "评价"
    if eval_dir.exists():
        for path in sorted(eval_dir.glob("*.xlsx")):
            result.append({"name": f"评价/{path.name}", "size": path.stat().st_size})
    trace_dir = run_dir / "trace_analysis"
    if trace_dir.exists():
        for path in sorted(trace_dir.iterdir()):
            if path.is_file():
                result.append({"name": f"trace_analysis/{path.name}", "size": path.stat().st_size})
    return result


def _active_run_job(run_name: str) -> bool:
    with _jobs_lock:
        return any(
            job.get("run_name") == run_name and job.get("status") == "running"
            for job in _jobs.values()
        )


def _backup_invalid_result(path: Path) -> None:
    if not path.exists():
        return
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(path.name + f".invalid_{stamp}")
    shutil.move(str(path), str(backup))


def _run_agent_batches(job_id: str, run_dir: Path, initial_done: int = 0) -> None:
    opencode = inspect_opencode(PROJECT_ROOT)
    if not opencode["available"]:
        raise RuntimeError(opencode["message"])
    if not opencode["agents_ready"]:
        raise RuntimeError(opencode["message"])

    specs = load_batch_specs(run_dir)
    total = len(specs) + 1 + initial_done  # predict prefix + batches + finish.
    done = initial_done
    _set_progress(job_id, 0, total)
    _append_log(job_id, f"已发现工艺批次 {sum(x.kind == 'process' for x in specs)} 个，B档动作描述批次 {sum(x.kind == 'action' for x in specs)} 个。")

    for spec in specs:
        if _job_cancelled(job_id):
            raise JobCancelled("用户取消了当前任务")
        valid, reason = result_is_valid(spec)
        if valid:
            _append_log(job_id, f"跳过已完成批次：{spec.kind}/{spec.batch_id}（{reason}）")
            done += 1
            _set_progress(job_id, done, total)
            continue

        if spec.output_path.exists():
            _append_log(job_id, f"发现无效的旧结果，先归档再重跑：{spec.output_path.name}（{reason}）")
            _backup_invalid_result(spec.output_path)

        label = "工艺分析" if spec.kind == "process" else "B档动作描述"
        _set_stage(job_id, f"OpenCode · {label}", f"{spec.batch_id} · {spec.input_path.name}")
        _append_log(job_id, f"开始 {label} 批次 {spec.batch_id}")
        command = build_opencode_command(opencode["binary"], spec, PROJECT_ROOT)
        returncode = _run_command(job_id, command, PROJECT_ROOT, timeout=timeout_seconds())
        if returncode != 0:
            raise RuntimeError(f"OpenCode 批次 {spec.batch_id} 退出码为 {returncode}")
        valid, reason = result_is_valid(spec)
        if not valid:
            raise RuntimeError(f"OpenCode 批次 {spec.batch_id} 未生成有效结果：{reason}")
        _append_log(job_id, f"✓ {label}批次 {spec.batch_id} 完成：{reason}")
        done += 1
        _set_progress(job_id, done, total)

    _set_stage(job_id, "Finish · 证据校验与交付表生成", "workflow.validate → Excel")
    _append_log(job_id, "所有 OpenCode 批次均已完成，开始执行 finish 校验与交付。")
    finish_cmd = [sys.executable, str(PROJECT_ROOT / "plc_cli.py"), "finish", "--run", str(run_dir.resolve())]
    returncode = _run_command(job_id, finish_cmd, PROJECT_ROOT, timeout=3600)
    if returncode != 0:
        raise RuntimeError(f"Finish 校验或交付生成失败，退出码为 {returncode}。请检查日志中的 invalid_issues。")
    status = _read_json(run_dir / "workflow_status.json") or {}
    if not list(run_dir.glob("*_步序采集表.xlsx")):
        detail = (status.get("workstation_xlsx") or {}).get("error", "未生成步序采集 Excel")
        raise RuntimeError(f"工作流校验通过，但交付 Excel 未生成：{detail}")
    done += 1
    _set_progress(job_id, done, total)
    _append_log(job_id, "✓ Finish 成功：最终表、复核队列和交付文件已生成。")


def _workflow_worker(job_id: str, config: dict) -> None:
    try:
        _set_stage(job_id, "Python · 源码解析", "plc_cli.py predict")
        _append_log(job_id, "开始执行 v9.25 predict：源码解析、候选生成、边界投影、A档描述与B档批次准备。")
        command = [
            sys.executable, str(PROJECT_ROOT / "plc_cli.py"), "predict",
            "--source", config["source"],
            "--pattern", config["pattern"],
            "--out", str(config["out_dir"]),
        ]
        if config["trace"]:
            command += ["--trace", config["trace"]]
        if config["manual"]:
            command += ["--manual", config["manual"]]
        returncode = _run_command(job_id, command, PROJECT_ROOT, timeout=3600)
        if returncode != 0:
            raise RuntimeError(f"predict 执行失败，退出码为 {returncode}")
        manifest_path = config["out_dir"] / "workflow_manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError("predict 返回成功，但未找到 workflow_manifest.json")
        _append_log(job_id, "✓ predict 完成，运行目录与批次输入已准备。")
        _set_progress(job_id, 1, 1)
        _run_agent_batches(job_id, config["out_dir"], initial_done=1)
        _finish_job(job_id, "completed", returncode=0)
    except JobCancelled as exc:
        _finish_job(job_id, "cancelled", str(exc))
    except Exception as exc:
        _append_log(job_id, f"✗ 工作流失败：{exc}")
        _finish_job(job_id, "failed", str(exc))


def _resume_worker(job_id: str, run_dir: Path) -> None:
    try:
        _append_log(job_id, "恢复现有运行：保留已通过结构检查的批次，只处理缺失或无效结果。")
        _run_agent_batches(job_id, run_dir)
        _finish_job(job_id, "completed", returncode=0)
    except JobCancelled as exc:
        _finish_job(job_id, "cancelled", str(exc))
    except Exception as exc:
        _append_log(job_id, f"✗ 恢复执行失败：{exc}")
        _finish_job(job_id, "failed", str(exc))


def _job_thread(job_id: str, target, *args) -> None:
    thread = threading.Thread(target=target, args=(job_id, *args), daemon=True)
    thread.start()


# ---------------------------------------------------------------------------
# Pages and system status
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/system/status", methods=["GET"])
def api_system_status():
    opencode = inspect_opencode(PROJECT_ROOT)
    version_path = PROJECT_ROOT / "VERSION"
    project_version = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else "9.25"
    try:
        flask_version = metadata.version("Flask")
    except metadata.PackageNotFoundError:
        flask_version = "未安装"
    return jsonify({
        "project_version": project_version,
        "python_version": platform.python_version(),
        "flask_version": flask_version,
        "opencode": opencode,
        "project_root": str(PROJECT_ROOT),
    })


# ---------------------------------------------------------------------------
# File browse
# ---------------------------------------------------------------------------
@app.route("/api/browse", methods=["POST"])
def api_browse():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "file")
    file_filter = data.get("filter", "")
    title = data.get("title", "选择文件")
    helper = WEB_DIR / "_browse.py"
    command = [sys.executable, str(helper), mode, file_filter, title]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 and result.stderr.strip():
            return jsonify({"path": "", "error": result.stderr.strip()}), 500
        return jsonify({"path": result.stdout.strip()})
    except subprocess.TimeoutExpired:
        return jsonify({"path": "", "error": "文件选择超时"}), 500
    except Exception as exc:
        return jsonify({"path": "", "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Full workflow: predict -> OpenCode process/action batches -> finish
# ---------------------------------------------------------------------------
@app.route("/api/workflow/start", methods=["POST"])
def api_workflow_start():
    data = request.get_json(silent=True) or {}
    try:
        config = _validate_workflow_input(data)
    except FileExistsError as exc:
        return jsonify({"error": str(exc)}), 409
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    if _active_run_job(config["run_name"]):
        return jsonify({"error": "该运行目录已有任务正在执行"}), 409

    opencode = inspect_opencode(PROJECT_ROOT)
    if not opencode["available"] or not opencode["agents_ready"]:
        return jsonify({"error": opencode["message"], "opencode": opencode}), 503

    job_id = _create_job("workflow", config["run_name"])
    _append_log(job_id, f"项目目录：{PROJECT_ROOT}")
    _append_log(job_id, f"输出目录：{config['out_dir']}")
    _append_log(job_id, f"OpenCode：{opencode['version']} · 批次 Agent 已就绪")
    _job_thread(job_id, _workflow_worker, config)
    return jsonify({"job_id": job_id, "run_name": config["run_name"]})


@app.route("/api/run/<name>/resume", methods=["POST"])
def api_workflow_resume(name):
    try:
        run_dir = _resolve_run_dir(name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    if not (run_dir / "workflow_manifest.json").is_file():
        return jsonify({"error": "该目录不是有效的 predict 运行目录"}), 404
    if _active_run_job(name):
        return jsonify({"error": "该运行目录已有任务正在执行"}), 409
    run_info = _run_stats(run_dir)
    if run_info.get("finished"):
        return jsonify({"error": "该运行已经完成，无需恢复"}), 409

    opencode = inspect_opencode(PROJECT_ROOT)
    if not opencode["available"] or not opencode["agents_ready"]:
        return jsonify({"error": opencode["message"], "opencode": opencode}), 503

    job_id = _create_job("resume", name)
    _job_thread(job_id, _resume_worker, run_dir)
    return jsonify({"job_id": job_id, "run_name": name})


# ---------------------------------------------------------------------------
# Legacy predict / finish endpoints (kept for scripts and older frontends)
# ---------------------------------------------------------------------------
@app.route("/api/predict", methods=["POST"])
def api_predict():
    data = request.get_json(silent=True) or {}
    try:
        config = _validate_workflow_input(data)
    except FileExistsError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    command = [
        sys.executable, str(PROJECT_ROOT / "plc_cli.py"), "predict",
        "--source", config["source"], "--pattern", config["pattern"], "--out", str(config["out_dir"]),
    ]
    if config["trace"]:
        command += ["--trace", config["trace"]]
    if config["manual"]:
        command += ["--manual", config["manual"]]
    job_id = _create_job("predict", config["run_name"])
    _job_thread(job_id, _legacy_command_worker, command)
    return jsonify({"job_id": job_id, "run_name": config["run_name"]})


def _legacy_command_worker(job_id: str, command: list[str]) -> None:
    try:
        _set_stage(job_id, "Python CLI", Path(command[1]).name)
        code = _run_command(job_id, command, PROJECT_ROOT, timeout=3600)
        if code != 0:
            raise RuntimeError(f"命令失败，退出码为 {code}")
        _finish_job(job_id, "completed", returncode=code)
    except JobCancelled as exc:
        _finish_job(job_id, "cancelled", str(exc))
    except Exception as exc:
        _append_log(job_id, f"✗ {exc}")
        _finish_job(job_id, "failed", str(exc))


@app.route("/api/finish", methods=["POST"])
def api_finish():
    data = request.get_json(silent=True) or {}
    name = str(data.get("run_name") or "").strip()
    try:
        run_dir = _resolve_run_dir(name)
        specs = load_batch_specs(run_dir)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    pending = [f"{x.kind}/{x.batch_id}" for x in specs if not result_is_valid(x)[0]]
    if pending:
        return jsonify({"error": "仍有未完成或无效批次：" + "、".join(pending[:8])}), 409
    if _active_run_job(name):
        return jsonify({"error": "该运行目录已有任务正在执行"}), 409
    job_id = _create_job("finish", name)
    command = [sys.executable, str(PROJECT_ROOT / "plc_cli.py"), "finish", "--run", str(run_dir)]
    _job_thread(job_id, _legacy_command_worker, command)
    return jsonify({"job_id": job_id, "run_name": name})


# ---------------------------------------------------------------------------
# Job polling and cancellation
# ---------------------------------------------------------------------------
@app.route("/api/job/<job_id>", methods=["GET"])
def api_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        sent = job.get("lines_sent", 0)
        new_lines = job["output"][sent:]
        job["lines_sent"] = len(job["output"])
        end_time = job.get("end_time", _now())
        elapsed = (end_time if job["status"] != "running" else _now()) - job["start_time"]
        return jsonify({
            "id": job_id,
            "type": job["type"],
            "status": job["status"],
            "stage": job.get("stage", ""),
            "current_item": job.get("current_item", ""),
            "progress": job.get("progress", {}),
            "new_lines": new_lines,
            "total_lines": len(job["output"]),
            "returncode": job.get("returncode"),
            "run_name": job.get("run_name"),
            "elapsed": round(elapsed, 1),
            "error": job.get("error"),
            "can_cancel": job["status"] == "running",
        })


@app.route("/api/job/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        if job["status"] != "running":
            return jsonify({"error": "任务已结束"}), 409
        job["cancel_requested"] = True
        proc = job.get("process")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    return jsonify({"success": True, "message": "已请求取消，正在安全终止当前子进程"})


# ---------------------------------------------------------------------------
# Run history, metrics, batches, tables and downloads
# ---------------------------------------------------------------------------
@app.route("/api/runs", methods=["GET"])
def api_runs():
    runs = []
    if OUTPUTS_DIR.exists():
        for directory in sorted(OUTPUTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            manifest = _read_json(directory / "workflow_manifest.json")
            status = _read_json(directory / "workflow_status.json")
            batches = _batch_status(directory, validate=False) if manifest else {"process": {}, "b_tier": {}}
            runs.append({
                "name": directory.name,
                "mtime": directory.stat().st_mtime,
                "source": (manifest or {}).get("source", ""),
                "finished": bool(status and status.get("success") and list(directory.glob("*_步序采集表.xlsx"))),
                "has_manifest": manifest is not None,
                "pending": (batches.get("process", {}).get("pending", 0) or 0) + (batches.get("b_tier", {}).get("pending", 0) or 0),
            })
    return jsonify(runs)


@app.route("/api/run/<name>/info", methods=["GET"])
def api_run_info(name):
    try:
        run_dir = _resolve_run_dir(name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"error": "运行目录不存在"}), 404
    return jsonify(_run_stats(run_dir))


@app.route("/api/run/<name>/pairs", methods=["GET"])
def api_run_pairs(name):
    try:
        run_dir = _resolve_run_dir(name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"error": "运行目录不存在"}), 404

    csv_path = run_dir / "final_prediction_pairs.csv"
    if not csv_path.is_file():
        csv_path = run_dir / "prediction_collection_pairs.csv"
    if not csv_path.is_file():
        return jsonify({"columns": [], "rows": [], "total": 0, "page": 1, "total_pages": 0})

    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            columns = list(reader.fieldnames or [])
            all_rows = list(reader)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    # Descriptions are keyed by start/end labels, not row ordinal: finish may
    # add, atomize or merge candidate rows and therefore change table ordering.
    desc_file = run_dir / "action_desc_final.csv"
    desc_queues: dict[tuple[str, str], deque] = defaultdict(deque)
    if desc_file.is_file():
        try:
            with desc_file.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    key = (str(row.get("开始标签", "")).strip(), str(row.get("结束标签", "")).strip())
                    desc = str(row.get("动作描述", "")).strip()
                    if desc:
                        desc_queues[key].append(desc)
        except Exception:
            pass
    for row in all_rows:
        if not str(row.get("动作描述", "")).strip():
            key = (str(row.get("开始标签", "")).strip(), str(row.get("结束标签", "")).strip())
            if desc_queues.get(key):
                row["动作描述"] = desc_queues[key].popleft()
    if "动作描述" not in columns:
        columns.insert(4, "动作描述")

    search = (request.args.get("search") or "").strip().lower()
    if search:
        all_rows = [
            row for row in all_rows
            if search in " ".join(str(value) for value in row.values()).lower()
        ]
    try:
        page = max(1, int(request.args.get("page", "1")))
        per_page = min(200, max(10, int(request.args.get("per_page", "100"))))
    except ValueError:
        return jsonify({"error": "分页参数无效"}), 400
    total = len(all_rows)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    return jsonify({
        "columns": columns,
        "rows": all_rows[start:start + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "source_file": csv_path.name,
    })


@app.route("/api/run/<name>/batches", methods=["GET"])
def api_run_batches(name):
    try:
        run_dir = _resolve_run_dir(name)
        specs = load_batch_specs(run_dir)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    result = {"process": [], "b_tier": []}
    for spec in specs:
        valid, reason = result_is_valid(spec)
        bucket = "process" if spec.kind == "process" else "b_tier"
        result[bucket].append({
            "batch_id": spec.batch_id,
            "route_id": spec.input_path.stem.replace(".input", "") if spec.kind == "action" else spec.batch_id,
            "has_input": spec.input_path.is_file(),
            "has_result": spec.output_path.is_file(),
            "valid": valid,
            "message": reason,
        })
    return jsonify(result)


@app.route("/api/run/<name>/batch/<batch_type>/<batch_id>", methods=["GET"])
def api_run_batch_detail(name, batch_type, batch_id):
    try:
        run_dir = _resolve_run_dir(name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"error": "运行目录不存在"}), 404
    if batch_type == "process":
        if not re.fullmatch(r"B\d{5}", batch_id):
            return jsonify({"error": "工艺批次 ID 无效"}), 400
        input_path = run_dir / "batches" / f"{batch_id}.input.json"
        output_path = run_dir / "batches" / f"{batch_id}.result.json"
    elif batch_type == "b_tier":
        if not re.fullmatch(r"batch_\d{3,}", batch_id):
            return jsonify({"error": "B档批次 ID 无效"}), 400
        input_path = run_dir / "batches_desc" / f"{batch_id}.input.json"
        output_path = run_dir / "batches_desc" / f"{batch_id}.result.json"
    else:
        return jsonify({"error": "未知批次类型"}), 400
    valid, message = (False, "输入文件不存在")
    if input_path.is_file():
        # Reuse the canonical spec validator where possible.
        try:
            spec = next(x for x in load_batch_specs(run_dir) if x.input_path == input_path.resolve())
            valid, message = result_is_valid(spec)
        except Exception as exc:
            message = str(exc)
    return jsonify({
        "batch_id": batch_id,
        "input": _read_json(input_path) if input_path.is_file() else None,
        "result": _read_json(output_path) if output_path.is_file() else None,
        "valid": valid,
        "message": message,
    })


@app.route("/api/run/<name>/files", methods=["GET"])
def api_run_files(name):
    try:
        run_dir = _resolve_run_dir(name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"error": "运行目录不存在"}), 404
    return jsonify(_list_run_files(run_dir))


@app.route("/api/run/<name>/download", methods=["GET"])
def api_run_download(name):
    try:
        run_dir = _resolve_run_dir(name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    filename = str(request.args.get("file") or "").replace("\\", "/").strip()
    if not filename:
        return jsonify({"error": "缺少 file 参数"}), 400
    relative = Path(filename)
    if relative.is_absolute() or ".." in relative.parts:
        return jsonify({"error": "非法文件路径"}), 400
    file_path = (run_dir / relative).resolve()
    try:
        file_path.relative_to(run_dir.resolve())
    except ValueError:
        return jsonify({"error": "非法文件路径"}), 400
    if not file_path.is_file():
        return jsonify({"error": "文件不存在"}), 404
    return send_file(str(file_path), as_attachment=True, download_name=file_path.name)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PLC_WEB_PORT", "5173"))
    host = os.environ.get("PLC_WEB_HOST", "127.0.0.1")
    url = f"http://{host}:{port}"
    print("\n  PLC Sequence Agent v9.25 — Web 控制台")
    print(f"  项目目录：{PROJECT_ROOT}")
    print(f"  访问地址：{url}\n")
    if os.environ.get("PLC_WEB_NO_BROWSER") != "1":
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    app.run(host=host, port=port, debug=False, threaded=True)
