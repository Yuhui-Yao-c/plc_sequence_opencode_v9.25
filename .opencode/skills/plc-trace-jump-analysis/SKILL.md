# Skill: plc-trace-jump-analysis

# PLC 运行时 Trace 跳变分析与预测步序时长回填

## 何时加载
用户提供了 PLC 运行时 trace 文件（TwinCAT Scope View 导出的 .trace），
需要把 trace 里的步序值跳变对应到已提取的预测步序对、用实测时长填入
交付表时加载本 skill。

不生成预测、不修改预测、不替代覆盖评价；trace 是运行时证据，不是
GT 答案，也不构成对源码迁移逻辑正确性的证明。

## 业务目标
1. 解析 trace 文件，提取每个被监测变量的步值跳变与稳定段时长。
2. 将预测步序对（开始标签 → 结束标签）与 trace 中的实际跳变匹配，
   给出该步序在运行时是否发生、发生次数、首次/末次/平均时长。
3. 把匹配到的实测时长回填到交付表的"时长(ms)"列，并在每个工位
   组块尾的"总时长"行求和。
4. 未匹配的预测对与 trace 独有跳变如实记入 review，不静默丢弃。

## Trace 文件格式
- TwinCAT Scope View XML 序列化（.NET XmlSerializer），UTF-16 LE with BOM。
- 每个变量是一个 `<TraceVariable VarName="..." VariableIndex="..." Type="System.Double">` 块。
- 每块包含 `<Values>v0,v1,v2,...,vN</Values>` 与 `<Timestamps>t0,t1,...,tN</Timestamps>`。
- 时间戳单位毫秒（ms），事件驱动采样（非固定周期）。
- `<StartTime>` 是 .NET ticks 绝对基准，时间戳是相对偏移；分析只关心相对差。
- 部分变量在本次 trace 中完全未活动（值恒为 0）——这是正常现象，不报错。
- 部分变量（如连续输送线 EM13V）可能从不归零，周期检测需排除这类变量。

## 跳变与稳定段
- 跳变（jump）：连续两个样本值不同，记 (var, from_value, to_value, timestamp_ms, sample_index)。
- 稳定段（segment）：连续相同值的样本区间，记 (var, value, start_ts, end_ts, duration_ms)。
- 值 0 = idle（非动作），稳定段单独统计；长 idle（默认 >5 秒）视为周期分界候选。

## 预测匹配规则
对 `final_prediction_pairs.csv` 每一行：
1. 解析 开始标签 → (var_start, val_start)；结束标签 → (var_end, val_end)。
2. 若 var_start ≠ var_end（跨变量对）：不匹配，进 review，标记 `cross_variable_pair`。
3. 若 var_start == var_end（同变量）：
   a. **精确匹配**：trace 中存在 from=val_start → to=val_end 的跳变。
   b. **经 0 中转匹配**：trace 中存在 from=val_start → 0 → val_end 的序列，
      且 0 段时长 < 中转阈值（默认 1000 ms）。算匹配，标注 `via_zero`。
   c. 以上均不匹配 → review，标注 `predicted_but_not_observed`。
4. 多周期重复跳变：记录 `occurrences`（发生次数）、首次时间、末次时间、
   平均时长、最小/最大时长。时长 = segment(val_end).start_ts - segment(val_start).start_ts。
5. trace 中有但预测无的跳变：进 review，标注 `trace_only_jump`。

## 时长定义
- 预测对 A→B 的时长 = trace 中 B 状态段的 start_ts − A 状态段的 start_ts。
- 即从 A 状态开始到 B 状态开始的时长，等同于 A 状态持续时长。
- 多周期取平均值；交付表显示平均时长（整数毫秒）。
- 经 0 中转的时长包含 0 段等待时间，与人工采集表的"开始时间→完成时间"口径一致。

## 隔离规则
| 规则 | 说明 |
|---|---|
| 不读人工表（GT） | trace skill 不读人工采集表；coverage 评价独立 |
| 不生成/修改预测 | trace 只验证已有预测，不新增/修改/删除预测对 |
| 不修改源码 | trace 是只读输入 |
| 评价隔离 | trace 匹配结果不回流 coverage 评价；coverage 仍只读预测+人工表 |
| trace ≠ 源码逻辑证明 | trace 证明"这一跳确实发生了"，不证明"源码迁移逻辑正确" |

## 文件与执行
**输入**：
- trace 文件路径（必需）
- final_prediction_pairs.csv（必需，匹配目标）
- step_comments.json / station_identity.json（可选，仅用于显示）

**输出**（run 目录下）：
| 文件 | 内容 |
|---|---|
| `trace_variables.json` | trace 元数据：变量列表、样本数、时间范围、活动/未活动 |
| `trace_jumps.csv` | 所有跳变：变量、from值、to值、时间戳(ms)、样本索引 |
| `trace_segments.csv` | 稳定段：变量、值、起始ms、结束ms、时长ms |
| `trace_cycles.json` | 检测到的生产周期边界（基于长 idle 分段） |
| `trace_prediction_mapping.csv` | **核心**：预测对 ↔ trace 跳变匹配（发生次数、首次时间、平均时长等） |
| `trace_step_runtime.csv` | 每对预测标签的实测时长（开始标签, 结束标签, 平均时长ms, 发生次数, 匹配类型） |
| `trace_review.csv` | 未匹配预测、trace 独有跳变、跨变量对、多周期冲突 |

**CLI 集成**：
```bash
# predict 接收 --trace（可选），存入 manifest
python plc_cli.py predict --source "源.xml" --pattern "..." --out "outputs/X" --trace "source/X/xxx.trace" --manual "人工表.xls"

# finish 检测 manifest 中的 trace，自动分析并回填时长
python plc_cli.py finish --run "outputs/X"
```

参数顺序：`XML地址 匹配格式 trace地址（可选） 人工表地址（可选）`。

## 边界情况
| 情况 | 处理 |
|---|---|
| 变量 0 跳变（未活动） | trace_variables.json 标记 `inactive: true`，不报错 |
| 变量无 0 值（连续运行） | 周期检测排除；段分析正常输出 |
| 长空闲（>5 秒） | 周期分界标志，该段不计入步序时长 |
| 多周期重复跳变 | 记录 occurrences + 首次/末次/平均/最小/最大时长 |
| trace 有但预测无 | review 标注 `trace_only_jump` |
| 预测有但 trace 无 | review 标注 `predicted_but_not_observed` |
| 跨变量预测对 | review 标注 `cross_variable_pair`，不强制匹配 |
| BOOL 标签（无 = 值） | review 标注 `non_state_label`，不匹配 |
| 采样间隔异常（>1 秒） | 标注 `sampling_gap`，不阻断分析 |

## 与现有 SKILL 的关系
| 现有 SKILL | 关系 |
|---|---|
| plc-boundary-normalization | trace 验证其预测的步序对，不改变边界规则 |
| plc-signal-tail-merge | trace 可验证合并区间是否连续发生，不改变合并逻辑 |
| plc-cross-fb-transport | trace 可验证跨 FB 触发→接收是否实际发生 |
| plc-feedback-successor | trace 可验证反馈后继是否实际执行 |
| plc-coverage-evaluation | 独立——coverage 读人工表，trace 读 .trace 文件，互不干扰 |

## v9.11 / v9.12 / v9.13 兼容
trace 时长是运行时实测，不改变原子化、合并、跨 FB、反馈后继的边界规则。
合并区间（signal_merge）的时长 = 合并后起止状态段的 start_ts 差，
包含内部信号/数据收尾耗时，与合并语义一致。
