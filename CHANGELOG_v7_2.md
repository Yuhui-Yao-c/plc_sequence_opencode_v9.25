# v7.2 时间采集边界 + 通配变量过滤 + 本地三项目回归

- `candidate_filter.py`: 变量符号级 glob 匹配，支持 `ST*.AutoStep[*]`、`EM*.Process_steps[*]` 和 `m_nNextAutoStep[*]`；开始或结束任一端命中即可。`[]` 按字面处理，`*`、`?` 是通配符；不根据动作描述、POU名称错误命中。
- `agent.py --pattern` 产生全量和筛选版CSV；`workflow.py prepare --pattern` 只把筛选候选送给OpenCode。`assemble` 另导出筛选后最终步序表，并保留 `final_sequence_unfiltered.csv` 审计。
- `timing_equivalence.py` 保守分析 `Step=100 AND Feedback` 与 `Step=110` 的“条件成立后扫描内迁移”关系，不声称事件时间戳无条件相等，也不将相邻状态猜测计为到位反馈。
- 加入10项新单元测试；旧测试保留。
- 本地回归输入为用户上传的三个项目 **候选CSV和人工标签**，不是原始XML；未在本轮重新验证源码解析器和GLM-5.2端到端工艺整理。
- 人工结果可能有不同清洗口径（尤其超声波本次输入实际解析161条），严禁用历史170条替换本次实际分母。
