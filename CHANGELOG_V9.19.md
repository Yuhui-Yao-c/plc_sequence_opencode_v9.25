# V9.19 版本变更说明 — finish 自动 A 档动作描述 + coverage 覆盖评价命令

日期：2026-10-30
基线版本：V9.18（盲区标色 + 网络注释提取 + 气缸/伺服词典扩展）
工程背景：V9.18 完成了标色和注释提取，但动作描述（action_describe.py）从未被 finish 自动调用，评价器（gen_coverage_eval）也未集成进 CLI。工程师要求"有人工表即评价"。本版将 A 档动作描述自动集成进 finish，新增 coverage 命令封装覆盖评价器，并对超声波跑完整工作流验证。
测试基线：8 failed / 282 passed（与 V9.18 基线一致，全程未引入新失败）。

## 变更清单

### 1. finish 自动生成 A 档动作描述（workflow.py + gen_workstation_xlsx.py）
- **workflow.py validate()** 在生成交付表前自动调用 `action_describe.cmd_a()`（纯源码事实模板，不需 LLM，只读 predict 产物，不接触 GT）。
- 若 `batches_desc/*.result.json` 存在（B 档已跑），自动调 `cmd_validate()` 合并；否则将 A 档复制为 `action_desc_final.csv` 供交付表读取。
- `workflow_status.json` 新增 `action_desc` 审计段（a_tier/b_tier/error）。
- **gen_workstation_xlsx.py** desc_map 加载增强：`action_desc_final.csv` 不存在时 fallback 到 `action_desc_a.csv`（A 档兜底）；列名适配（A档有"A档描述"列，自动重命名为"动作描述"）。

### 2. 新增 coverage 命令（plc_cli.py + 评价器/gen_coverage_eval.py）
- `plc_cli.py` 新增 `coverage` 子命令，封装 `评价器/gen_coverage_eval.py`。
- `gen_coverage_eval.py` 核心逻辑提取为 `main_with()` 函数（可被 CLI 直接调用，不需 argparse）。
- 用法：`python plc_cli.py coverage --prediction ... --manual ... --out ... --proc-name ... --kind codesys|mylar|auto`
- 修复了 `out.mkdir()` 把 xlsx 文件路径当目录创建的 bug（改为 `out.parent.mkdir()`）。
- 遵循预测/评价隔离原则：coverage 是独立命令，不在 finish 里自动跑（AGENTS.md 要求"预测与评价建议在独立会话执行"）。

### 3. 版本号 9.18.0 → 9.19.0

## 超声波完整工作流验证

| 步骤 | 命令 | 结果 |
|---|---|---|
| finish | `python plc_cli.py finish --run outputs/超声_XM22PPM_L5B` | success=true；A档615行（机械动作237/步进155/动作+信号127/信号68/迁移28）；交付表61工位组 |
| 交付表动作描述 | 615/615 行有A档描述，0模板文本 | 气缸"张开/夹紧/气动下降"、伺服"AbsEx"、步进保持 |
| 标色 | D=361 B=28 C=0 A=0（A档覆盖后模板文本归零） | 浅红边界+浅橙跨工序 |
| coverage | `python plc_cli.py coverage ...` | 覆盖率 94.1%（160/170，未覆盖10） |

### 覆盖率详情
- 直接匹配 68 / 多段可合并 25 / 已覆盖·拆分粒度不同 47 / 已覆盖·写法不一致 20 / 未覆盖 10
- 未覆盖根因：拆分粒度不同 47 / NOT离开写法 20 / 跨工位转移·程序无对应 6 / 端点存在但路径不连通 4

## 为什么之前没跑

| 问题 | 原因 | V9.19 解决方案 |
|---|---|---|
| 动作描述没跑 | action_describe.py 是独立 CLI，从未被 finish 调用 | finish 自动调 cmd_a()（A档），B档可选 |
| 评价器没集成 | gen_coverage_eval.py 是独立脚本，未进 CLI | 新增 coverage 命令封装 |
| 交付表动作描述列空 | desc_map 只读 action_desc_final.csv（从不存在） | fallback 到 action_desc_a.csv |

## 与工作流的关系
- A 档动作描述是源码事实模板（机构词[文档]+动作词[词典]+引用），不读 GT/人工表，符合预测隔离。
- B 档（LLM 工艺语义）仍需 agent 逐批处理，不能在 CLI 自动完成；finish 检测到 batches_desc/*.result.json 时自动合并。
- coverage 命令读预测 + 人工表，不修改预测，独立于 finish。
