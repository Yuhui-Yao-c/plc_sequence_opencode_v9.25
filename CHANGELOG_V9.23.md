# CHANGELOG V9.23 — 运行时 trace 跳变分析与时长回填

## 新增功能

### 1. trace_jump_analyzer.py（新建）
解析 TwinCAT Scope View 导出的 `.trace` 文件（UTF-16 LE XML，.NET XmlSerializer 格式），
提取每个 `TraceVariable` 的步值跳变（jump）与稳定段（segment），将预测步序对与
trace 中的实际跳变匹配，给出该步序在运行时的发生次数、首次/末次/平均/最小/最大时长。

**Trace 格式**：
- UTF-16 LE with BOM
- 每个变量 `<TraceVariable VarName="..." Type="System.Double">` 含 `<Values>` 和 `<Timestamps>`
- 时间戳单位毫秒，事件驱动采样（非固定周期）
- 部分变量可能完全未活动（值恒为 0），不报错

**匹配规则**（优先级递减）：
1. **精确匹配**（exact）：trace 中存在 from=val_start → to=val_end 的直接跳变
2. **经0中转匹配**（via_zero）：trace 中存在 from→0→to 序列，且 0 段时长 ≤1000ms
3. **松散匹配**（loose）：trace 中存在 from→to 但中间有其他状态
4. 以上均不匹配 → review 标注 `predicted_but_not_observed`

**时长定义**：预测对 A→B 的时长 = trace 中 B 状态段的 start_ts − A 状态段的 start_ts
（即从 A 状态开始到 B 状态开始的时长，等同于 A 状态持续时长）。多周期取平均值。

**输出文件**（run 目录下 `trace_analysis/` 子目录）：
| 文件 | 内容 |
|---|---|
| trace_variables.json | 变量元数据：样本数、时间范围、活动/未活动、跳变数、唯一值 |
| trace_jumps.csv | 所有跳变：变量、from值、to值、时间戳(ms)、样本索引 |
| trace_segments.csv | 稳定段：变量、值、起始ms、结束ms、时长ms |
| trace_cycles.json | 检测到的生产周期边界（基于长 idle >5s 分段） |
| trace_prediction_mapping.csv | 预测对 ↔ trace 跳变匹配详情 |
| trace_step_runtime.csv | 每对预测标签的实测时长（开始标签, 结束标签, 平均时长ms, 发生次数, 匹配类型） |
| trace_review.csv | 未匹配预测、trace 独有跳变、跨变量对、BOOL标签 |

### 2. plc_cli.py predict --trace 参数
predict 子命令新增 `--trace` 可选参数，传入 .trace 文件路径，存入 workflow_manifest.json。
参数顺序：`--source XML --pattern 匹配格式 --out 输出目录 --trace trace地址 --manual 人工表地址`。
finish 检测到 manifest 中的 trace 时自动运行分析。

### 3. workflow.py validate() 集成
finish 流程在生成交付表之前，检测 manifest.trace，自动调用 trace_jump_analyzer.analyze()，
将分析结果写入 `trace_analysis/` 子目录，并将 stats 记入 workflow_status.json。

### 4. gen_workstation_xlsx.py 时长回填
- 新增 `load_trace_runtime(run)`：从 `trace_analysis/trace_step_runtime.csv` 读取 (开始标签, 结束标签) → 平均时长ms 映射
- 交付表 D 列（时长(ms)）：从 trace_runtime_map 回填，无 trace 时留空
- 总时长行：累加该工位组所有数据行的 trace 时长，有 trace 数据时填入合计
- note 说明文字：有 trace 时显示"时长(ms)与总时长由运行时trace跳变分析自动回填"

### 5. SKILL.md
新建 `.opencode/skills/plc-trace-jump-analysis/SKILL.md`，定义 trace 分析的加载条件、
业务目标、格式说明、跳变/稳定段定义、匹配规则、时长定义、隔离规则、文件输出、边界情况。

## 隔离规则
| 规则 | 说明 |
|---|---|
| 不读人工表（GT） | trace skill 不读人工采集表；coverage 评价独立 |
| 不生成/修改预测 | trace 只验证已有预测，不新增/修改/删除预测对 |
| 不修改源码 | trace 是只读输入 |
| 评价隔离 | trace 匹配结果不回流 coverage 评价 |
| trace ≠ 源码逻辑证明 | trace 证明"这一跳确实发生了"，不证明"源码迁移逻辑正确" |

## 验证结果（转接片）
- trace 文件：ZJPJIANKONG0906.trace（9MB，UTF-16，30 变量，15772 样本/变量）
- 预测对：1488 条
- 匹配成功：181 条（exact:102, via_zero:60, loose:19）
- 未匹配：1307 条（trace 只监测 30 个变量中的 25 个活动变量，预测覆盖更多变量）
- trace 独有跳变：728 条（如实记入 review）
- 交付表时长回填：133 个数据行有实测时长，53 个总时长行有合计
- 覆盖率：98.7%（157/159，与无 trace 时一致，trace 不影响覆盖率）
- 测试基线：8 failed / 282 passed（无回归）

## 版本号
- plc_cli.py: 9.22.0 → 9.23.0
- workflow.py: 9.22 → 9.23
- prediction_provenance.json: 新增 trace 字段

## 文件变更
| 文件 | 变更 |
|---|---|
| trace_jump_analyzer.py | 新建（解析+匹配引擎，约 300 行） |
| .opencode/skills/plc-trace-jump-analysis/SKILL.md | 新建 |
| plc_cli.py | predict 新增 --trace 参数，version 9.23.0 |
| workflow.py | create() 接收 trace 参数，validate() 集成 trace 分析，集成 cmd_btier_fallback 兜底 |
| action_describe.py | 新增 cmd_btier_fallback() 函数，B 档 result.json 缺失时自动生成规则描述 |
| gen_workstation_xlsx.py | 新增 load_trace_runtime()，时长列与总时长行回填 |
| AGENTS.md | 新增 v9.23 路由节 |

## B 档处理一致性（不允许规则脚本冒充 LLM）
**原则**：所有工序的 B 档动作描述必须走同一条路径——agent 逐批处理 `batches_desc/*.input.json` 写 `result.json`，不允许规则脚本兜底冒充 LLM 输出。

**修复**：
- 删除 workflow.py 中的 `cmd_btier_fallback` 调用，finish 不再自动生成 result.json
- finish 如实报告 B 档状态：`b_tier_input_count`、`b_tier_result_count`、`b_tier_pending`（待 agent 处理的批次数）
- B 档未处理时 finish 报警："B档未处理: N批 input.json 待 agent 逐批处理写 result.json 后重跑 finish；当前交付表动作描述为空（仅 A 档步注释）"
- 转接片 31 批 1208 行由 agent 逐批处理生成 result.json（与超声/包mylar/顶盖焊同一路径）

**五工序 B 档一致性**：

| 工序 | input | result | 描述来源 | 有描述 | 含英文 |
|---|---|---|---|---|---|
| 超声 | 11 | 11 | B档421+A档194 | 609/615 | 0 |
| 包mylar | 2 | 2 | A档1836+B档51 | 1887/1887 | 0 |
| 顶盖焊 | 27 | 27 | B档1057+A档12 | 1066/1069 | 0 |
| 转接片 | 31 | 31 | B档1208+A档280 | 1488/1488 | 26(A档CCD/XY/XR) |
