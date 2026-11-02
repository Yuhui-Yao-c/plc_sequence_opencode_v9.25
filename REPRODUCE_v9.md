# v9 可复现运行方法（Windows / OpenCode）

1. 解压后安装：`python -m pip install -r requirements.txt`。
2. 在仓库根目录运行 `opencode`；对话框输入 `D:\PLC\source.xml | ST*.AutoStep[*]`。
3. 或直接执行 `python workflow.py prepare "D:\PLC\source.xml" -o outputs\run_v9 --pattern "ST*.AutoStep[*]"`，之后由OpenCode按工作流处理所有批次；`python workflow.py status outputs\run_v9` 检查，最后 `python workflow.py assemble outputs\run_v9`。
4. 独立执行 `python agent.py "D:\PLC\source.xml" -o outputs\run_v9_candidates --pattern "ST*.AutoStep[*]"` 只输出候选，不调用大模型。
5. 新增局部使能链：`plcopen_control_relations.csv` 和 `plcopen_control_summary.json`。如遇 SMC2 格式则不生成PLCopen专属文件。
6. 如果有人工标签，运行 `python temporal_audit.py outputs\run_v9\sequence_candidates.csv --truth reference.tsv -o outputs\run_v9\audit`。人工答案不参与源码候选提取。

**重要：** `wired_move_guard_local` 只能证明局部的 EN—EQ 连接，不能当作动作完成或采集时间戳同时成立。复杂的ST IF/CASE和跨程序控制仍需要源码审查。

## 本次测试口径

三项目的完整原始PLC XML在这次环境中不可用；随附 Excel 为 v8 本地基线及 v9 测试口径说明，**不是 v9 三项目完整XML重新运行结果**。
