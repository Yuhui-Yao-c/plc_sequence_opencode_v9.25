# v8 Windows 使用与复现

安装依赖：`python -m pip install -r requirements.txt`。在仓库根目录执行 `opencode`，直接对话输入：

`D:\PLC\超声波.xml | ST*.AutoStep[*]`

默认 OpenCode Agent 读取仓库 `AGENTS.md`，调用 `workflow.py prepare`，按 `workflow_manifest.json` 的批次写结果，然后 `status` 和 `assemble`。**只有OpenCode完成所有批次并通过assemble审计才会生成`final_sequence.csv`**。

如果想独立复现 Python 产出：

```powershell
python workflow.py prepare "D:\PLC\超声波.xml" -o outputs\run1 --pattern "ST*.AutoStep[*]"
python temporal_audit.py outputs\run1\sequence_candidates.csv --truth "D:\PLC\人工标签.tsv" -o outputs\run1\v8_audit
```

输出：`sequence_candidates.csv` 全量、`sequence_candidates_filtered.csv` 过滤候选、`event_graph.csv` 事件、`timing_relations.csv` 待核验的计时关系、`v8_audit/annotation_diagnosis.csv` 待核验人工对照。

没有人工标签时省略`--truth`；人工仅用于评估，不参与候选生成。人工可能有误，但缺少正面源码反证就不能标记“人工错误”。

```powershell
python workflow.py status outputs\run1
python workflow.py assemble outputs\run1
```

假如已生成`final_sequence.csv`，另用`compare_local.py`按原始候选与最终表分别对照人工，不能混淆两个阶段的指标。

## 已知限制

- 目前没有实现完整的PLCopen梯形图连接链及所有执行路径的可执行性证明；v8的timing关系仍为需要核验的线索。
- 回归数据源为三份本地导出的候选和事件图，不是重新读取三个完整PLC工程；不声称其指标是v8源码提取覆盖率。
