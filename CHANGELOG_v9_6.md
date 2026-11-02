# v9.6 回归修复
- 恢复先全量候选，后变量模式过滤，最后工艺语义整理的次序。
- 新增 `prediction_candidate_pairs.csv` 和 `final_prediction_pairs.csv`，消除工艺筛选导致主预测被截断的问题。
- `evaluate --xml` 可独立从PLCopen原XML提取有连线证据的迁移边用于GT候选搜索；不写回预测，不接受GT做预测。
- 仍不把有连线迁移等同于采集时间严格等价。
- 修复仅基于历史代码和局部测试；三个原始工程XML未提供则不声明覆盖率改善。
