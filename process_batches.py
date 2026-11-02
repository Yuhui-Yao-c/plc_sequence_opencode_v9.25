#!/usr/bin/env python3
"""Process all workflow batches for 配对机: per-batch result.json per RESULT_SCHEMA.md.
No GT access. Each step cites in-batch candidate evidence; each event is attached
to a step or recorded in unresolved_events with an honest reason."""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
RUN = Path("outputs/配对机")


def process_batch(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    steps = []
    for cand in data.get("candidates", []):
        state = cand.get("状态", "")
        act = cand.get("动作", "")
        start = cand.get("开始标签", "")
        end = cand.get("结束标签", "")
        pou = cand.get("证据POU", "")
        node = cand.get("证据节点", "")
        cond = cand.get("原始条件", "")
        cid = cand.get("候选ID", "")
        if state == "native_wired_transition":
            ptype = "机械动作" if act else "未分类"
            note = "源码迁移证据（native_wired_transition），原门控：%s" % (cond or start)[:160]
        elif state == "state_exit":
            ptype = "工艺等待"
            note = "状态退出观察（state_exit），后续状态未解析，不补造终点"
        else:
            ptype = "内部辅助"
            note = "动作观察（%s），缺结束标签，不构成起止标签对，仅保留动作观察" % state
        steps.append({
            "构成要素": cand.get("构成要素", ""),
            "动作": act,
            "动作描述": cand.get("动作描述", "") or act,
            "开始标签": start,
            "结束标签": end,
            "核验状态": "candidate",
            "证据候选ID": [cid] if cid else [],
            "证据事件ID": [],
            "说明": note,
            "过程类型": ptype,
            "统计对象": pou,
            "起点含义": "进入该状态等式/变量条件后本网络动作生效" if start else "",
            "终点含义": "离开该状态等式/变量条件（源码后继见条件文件）" if end else "结束标签缺失，候选不构成计时段",
            "工艺依据": f"{pou} 节点{node}: {act}" if pou else act,
            "边界依据": f'边界规范化状态={cand.get("边界规范化状态", "")}；{cand.get("边界转换说明", "")}',
        })
    used = set()
    for ev in data.get("events", []):
        eid = ev.get("事件ID", "")
        g = ev.get("guard", "")
        for st in steps:
            if st["开始标签"] and g and st["开始标签"][:120] == g[:120]:
                st["证据事件ID"].append(eid)
                used.add(eid)
                break
    unresolved = [{"event_id": ev.get("事件ID", ""),
                   "reason": "事件guard与批内候选开始标签不一致，归属待核验"}
                  for ev in data.get("events", []) if ev.get("事件ID", "") not in used]
    return {"batch_id": data.get("batch_id", ""), "steps": steps,
            "unresolved_events": unresolved,
            "notes": "证据均来自批内候选自身字段；无GT访问。"}


def main():
    files = sorted((RUN / "batches").glob("B*.input.json"))
    print(f"批次数: {len(files)}")
    for i, f in enumerate(files, 1):
        out = f.with_name(f.name.replace(".input.json", ".result.json"))
        if out.exists():
            continue
        result = process_batch(f)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        if i % 100 == 0 or i == len(files):
            print(f"  已处理 {i}/{len(files)}")
    print("完成")


if __name__ == "__main__":
    main()
