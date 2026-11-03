#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic primitives called by the OpenCode Web workflow controller.

The Flask HTTP layer never runs this module. The OpenCode controller invokes
predict/status/finish in order and delegates semantic batches to subagents.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from web_opencode import load_batch_specs, result_is_valid

ROOT = Path(__file__).resolve().parent
OUTPUTS = (ROOT / 'outputs').resolve()


def run_directory(name: str) -> Path:
    if not re.fullmatch(r'[\w\u4e00-\u9fff .()-]{1,100}', name) or name in ('.', '..'):
        raise ValueError('无效运行名称')
    run = (OUTPUTS / name).resolve()
    if run.parent != OUTPUTS:
        raise ValueError('运行目录必须位于 outputs 下')
    return run


def read_request(run: Path) -> dict:
    request_file = run / 'web_request.json'
    if not request_file.is_file():
        raise FileNotFoundError('缺少 web_request.json，请从已有 manifest 恢复批次或重新创建运行')
    data = json.loads(request_file.read_text(encoding='utf-8'))
    if Path(data.get('out', '')).resolve() != run:
        raise ValueError('请求清单中的输出目录与当前运行不一致')
    return data


def execute(command: list[str]) -> int:
    print('运行 Python 工作流原子能力：' + str(Path(command[1]).name) + ' ' + command[2], flush=True)
    return subprocess.run(command, cwd=str(ROOT), check=False).returncode


def predict(run: Path) -> int:
    if (run / 'workflow_manifest.json').is_file():
        print(json.dumps({'status':'existing_manifest','run':str(run)},ensure_ascii=False))
        return 0
    req = read_request(run)
    source = Path(req['source']).resolve()
    if not source.is_file() or source.suffix.lower() not in {'.xml', '.smc2', '.txt'}:
        raise ValueError('源码不存在或格式不支持')
    python = str(req.get('python') or sys.executable)
    command = [python, str(ROOT / 'plc_cli.py'), 'predict',
               '--source', str(source), '--pattern', str(req.get('pattern') or '*'), '--out', str(run)]
    if req.get('trace'):
        trace = Path(req['trace']).resolve()
        if not trace.is_file():
            raise FileNotFoundError(f'Trace 不存在：{trace}')
        command.extend(['--trace', str(trace)])
    if req.get('manual'):
        manual = Path(req['manual']).resolve()
        if not manual.is_file():
            raise FileNotFoundError(f'人工表不存在：{manual}')
        command.extend(['--manual', str(manual)])
    code = execute(command)
    if code != 0:
        return code
    if not (run / 'workflow_manifest.json').is_file():
        raise RuntimeError('predict 返回成功但没有输出 workflow_manifest.json')
    if not (run / 'action_desc_a.csv').is_file() or not (run / 'batches_desc').is_dir():
        raise RuntimeError('predict 未完整生成 A 档动作描述与 B 档批次目录，无法启动语义分析')
    return 0


def state(run: Path) -> dict:
    if not (run / 'workflow_manifest.json').is_file():
        return {'need_predict': True, 'run': str(run), 'completed': 0, 'total': 0, 'pending_count': 0}
    specs = load_batch_specs(run)
    pending=[]
    for spec in specs:
        valid, reason = result_is_valid(spec)
        if not valid:
            pending.append({
                'type': spec.kind, 'batch_id':spec.batch_id, 'agent':spec.agent,
                'input': str(spec.input_path), 'output':str(spec.output_path),
                'reason': reason,
            })
    finished_data = {}
    status_path = run / 'workflow_status.json'
    if status_path.is_file():
        try:
            finished_data = json.loads(status_path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
    excel = list(run.glob('*_步序采集表.xlsx'))
    latest_batch_mtime = max((spec.output_path.stat().st_mtime for spec in specs if spec.output_path.is_file()), default=0.0)
    delivery_is_fresh = bool(excel) and status_path.is_file() and all(
        path.stat().st_mtime >= latest_batch_mtime for path in [excel[0], status_path]
    )
    return {
        'need_predict': False, 'run': str(run),
        'total':len(specs), 'completed':len(specs)-len(pending), 'pending_count':len(pending),
        'next_pending':pending[0] if pending else None,
        'ready_for_finish':bool(specs) and not pending and (run/'action_desc_a.csv').is_file(),
        'finished':bool(specs) and not pending and bool(finished_data.get('success')) and delivery_is_fresh,
        'workflow_success':bool(finished_data.get('success')),
        'excel':excel[0].name if excel else None,
    }


def finish(run: Path) -> int:
    info = state(run)
    if not info.get('ready_for_finish'):
        raise RuntimeError('禁止提前 finish：缺少已完成批次、有效结果或 A 档描述')
    if info.get('finished'):
        print(json.dumps({'status':'already_finished','excel':info['excel']},ensure_ascii=False))
        return 0
    req_path = run/'web_request.json'
    python=sys.executable
    if req_path.is_file():
        python = str(read_request(run).get('python') or python)
    code = execute([python, str(ROOT/'plc_cli.py'),'finish','--run',str(run)])
    if code:
        return code
    result=state(run)
    if not result['finished']:
        raise RuntimeError('finish 退出成功，但缺少校验成功状态或最终 Excel')
    print(json.dumps({'status':'completed','excel':result['excel']},ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='OpenCode 工作流可调用原子能力')
    parser.add_argument('action',choices=('predict','status','finish'))
    parser.add_argument('--run',required=True)
    args=parser.parse_args()
    run=run_directory(args.run)
    if args.action=='predict': return predict(run)
    if args.action=='finish': return finish(run)
    print(json.dumps(state(run),ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'ERROR: {error}',file=sys.stderr)
        sys.exit(2)
