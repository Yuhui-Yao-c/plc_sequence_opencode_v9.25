# 对话单指令与本地检查

## 用户的单指令
在仓库根目录启动 `opencode` 并输入：

`D:\PLC\超声波.xml | ST*.AutoStep[*]`

OpenCode 使用 `workflow.py prepare` 的 `--pattern` 参数提取并编制批次，审核后 assemble。完整原始候选始终保留。

也可手动：

```bash
python workflow.py prepare "D:\PLC\超声波.xml" -o outputs/ultrasound --pattern "ST*.AutoStep[*]"
python workflow.py status outputs/ultrasound
# 只有所有批次完成后
python workflow.py assemble outputs/ultrasound
```

想只进行筛选：

```bash
python candidate_filter.py outputs/ultrasound/sequence_candidates.csv -o outputs/ultrasound/filtered.csv --pattern "ST*.AutoStep[*]"
```

## 运行时间边界
`Step=100 AND PosOK` 与 `Step=110` 通常不能在严格布尔逻辑上认为相等。对PLC梯形图，MOVE 可能由其他串联触点控制，写入在扫描特定时刻发生，采集模块又可能只周期读到更新后的 `Step`。只可报告“满足完整门控时发生相应状态迁移；可能存在一个扫描周期或采集周期的偏移”。若直接用于CT，需要明确设置统一采集时序并验证采集机制。

## 数据回归限制
上传三组 `sequence_candidates.csv`、`event_graph.csv`、`final_sequence.csv`，本轮匹配指标针对上传的**候选表**；未将基于GLM的 `final_sequence.csv` 与候选表混算。`event_graph.csv` 中的 `co_location_only` 明确不能证明动作反馈因果。
