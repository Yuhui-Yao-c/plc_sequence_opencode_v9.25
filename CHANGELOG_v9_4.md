# v9.4 工艺动作优先与证据筛选

- 新增 `process_semantics.py`，将候选标为执行机构、工艺检测/等待、纯状态假设、待验证局部迁移等类别；输出 `candidate_process_audit.csv`, `process_focus_queue.csv`, `state_seed_trace_queue.csv`, `process_focus_metrics.json`。
- `workflow.py prepare` 自动生成工艺审查队列；不会从候选总池删除任何记录。
- `workflow.py assemble` 保留完整 `final_sequence.csv`，新增工艺优先 `final_process_steps.csv`，未晋级项放入 `process_review_queue.csv`；这是基于模型已有工艺描述和来源事件的保守筛选，不是完整PLC动作证明。
- 更新OpenCode默认协议：EQ作种子，BFS看相关元件，但必须解析连接语义，纯状态邻接不作为真实工艺动作。
- GT评价路径与预测隔离，已有一对一彩色评价功能保持不变。
- 三项目回归利用之前上传的候选文件，没有重新执行三个大XML和大模型端到端流程。

**风险提示**：PLCopen旧候选往往只有相邻状态，此时重点在 `state_seed_trace_queue.csv` 回查原XML，不能依赖动作关键词过滤直接生成最终表。
