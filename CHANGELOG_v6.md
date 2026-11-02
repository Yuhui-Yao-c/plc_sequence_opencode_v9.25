# v6 更新（2026-10-21）
- 新增跨格式 `event_graph_v6.py`，包含 source_unit、guard、command、arguments、feedback、next_state、evidence 列。
- PLCopen LD/FBD 非标准块调用与线圈事件提取；ST布尔赋值事件提取。
- SMC2 JSON网络调用/线圈与反馈统一提取。
- 候选层追加 action_event_unverified 和 feedback_same_body_hypothesis 两类带审计标签的候选。
- 新增 benchmark_v6.py 及 export_match_excel.py，生成逐条匹配 Excel。
- 保留真实性限制：以上新增关系不等于已证明的控制流、完成时刻或全部物理动作召回。
