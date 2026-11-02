# V9.20 版本变更说明 — 交付表精简 + 工位分组修复 + A档优先源码步注释 + --manual自动评价

日期：2026-10-30
基线版本：V9.19（finish 自动 A 档动作描述 + coverage 覆盖评价命令）
工程背景：V9.19 虽完成 A 档自动化与 coverage 命令，但交付存在三个问题——(1) 交付表 12 列过宽且含大量空列；(2) codesys 数组型状态机（m_nNextAutoStep[N]）导致 1887 行塌缩成 1 个工位组；(3) A 档完全忽略源码步注释（覆盖率 97.5%），只堆砌英文变量名；此外评价器与工作流未打通。本版针对性修复。
测试基线：8 failed / 282 passed（与 V9.19 基线一致，全程未引入新失败）。

## 变更清单

### 1. 交付表精简为 7 列 + 染色简化（gen_workstation_xlsx.py）
- **列结构**：12 列 → 7 列（B-H），删除 动作/开始适用/动作时间/完成时间/目标时长 共 5 列。
- 保留列：构成要素 | 动作描述 | 时长(ms) | 动作变量（引用） | 开始标签 | 结束标签 | 备注。
- **染色简化**：4 色优先级（D红/B橙/C蓝/A黄）→ 单一淡黄色（FFF9E6），仅对 D（边界待核验）和 A（动作描述不完整）染色；B（跨工序步）和 C（合并区间）不再染色——二者本质不是错误。
- **标注方式**：动作描述后追加 `[D-边界待核验]` `[A-动作描述不完整]` 文字标注，替代多色方案。
- 说明栏（note）同步更新为淡黄色图例说明。

### 2. 数组型状态机工位分组修复（station_identity.py + gen_workstation_xlsx.py）
- **问题**：codesys 工程 `m_nNextAutoStep[N]`（N=2~20）共用一个变量根，station_identity 只产生 1 条记录，交付表 1887 行全归 1 个工位组。
- **station_identity.py 新增**：
  - `_extract_controller_pou_map()`：从 prediction_candidate_pairs.csv 提取 控制器×证据POU 的频次映射。
  - `_pou_area_to_station()`：从 POU 区域名（如 `EM02_电芯上料_滚轮线_L02_L04`）提取工位编号和中文名。
  - `build_identity()` 数组展开：对 `m_nNextAutoStep[N]` 这类多下标控制器，每个下标独立建立身份，取该下标最高频的 POU 区域作为工位名来源。
- **gen_workstation_xlsx.py 新增**：
  - `workstation()` 优先从 `构成要素` 的 POU 路径直提（`EMxx_name::SCxx_...`），无需 station_identity 也能正确分组。
  - `_变量根` 不再 strip 数组下标，保留 `m_nNextAutoStep[2]` 形式以便 si_map 精确查找。
  - `_row_sort()` 支持数组索引键（先查带下标键，再查裸根 fallback）。
- **效果**：包mylar 从 1 个工位组 → 20 个工位组（EM01_物流线 … EM20_治具回流），全部中文命名。

### 3. A 档优先使用源码步注释（action_describe.py）
- **问题**：A 档 `cmd_a()` 只读动作变量映射，从不读 `final_prediction_pairs.csv` 的 `动作描述`列——而该列在 codesys 工程中携带源码步注释，覆盖率 97.5%。
- **新增 `clean_step_comment()`**：
  - 去掉 `步N-` / `N-` 前缀（如 `步0-模式跳转` → `模式跳转`）。
  - 取多行注释的第一行。
  - **过滤工作流模板文本**（"相邻已观察状态"、"源码局部状态迁移"、"触发...到达信号...参与接收条件"等）——这些不是源码步注释，需 fallback 到变量翻译。
- **`cmd_a()` 优先级**：① 有源码步注释 → 清洗后使用；② 无注释但有动作变量 → 变量翻译（原逻辑）；③ 无注释无变量 → 步进保持/迁移模板。
- **效果**（包mylar）：步注释 1836 / 变量翻译 39 / 模板 12（改前：变量翻译 100%）。
- **不退化保证**（超声）：超声的 `动作描述`列全是工作流模板，`clean_step_comment()` 过滤后步注释=0，全部走变量翻译——与改前完全一致（变量翻译 432 / 模板 183）。

### 4. predict --manual 可选参数 + finish 自动 coverage（plc_cli.py + workflow.py）
- **predict 新增 `--manual`**：可选参数，传入人工采集表路径，存入 `workflow_manifest.json` 的 `manual` 字段。
- **finish 自动 coverage**：`validate()` 检测 manifest 有 `manual` 且文件存在时，自动调用 `gen_coverage_eval.main_with()` 运行覆盖评价，结果写入 `评价/<工序>_覆盖评价.xlsx`，状态记入 `workflow_status.json` 的 `coverage` 段。
- 不传 `--manual` 时行为与之前完全一致；传了则 finish 自动评价。
- 遵循预测/评价隔离：predict 不读人工表内容，只记录路径；coverage 在 finish 阶段（批次完成后）才读。

## 验证结果

### 包mylar 完整验证

| 指标 | V9.19（改前） | V9.20（改后） |
|---|---|---|
| 工位组数 | 1（塌缩） | **20**（EM01-EM20 全中文） |
| 描述来源 | 变量翻译 100% | **步注释 97.5%** + 变量翻译 2% + 模板 0.6% |
| 描述样例 | `步进保持（m_nNextAutoStep[0]: 0→10）` | `模式跳转` |
| 描述样例 | `InOut·=iScanNgCount_A; InOut·=MachineNum[N]...` | `等待上位机扫码完成结果` |
| 描述样例 | `Auto_Threadmedian[0]·BoolData[N]` | `屏蔽判断` |
| 覆盖率 | 未运行 | **99.7%（329/330）** |

### 超声不劣化验证

| 指标 | V9.19（改前） | V9.20（改后） |
|---|---|---|
| 工位组数 | 61 | **61**（不变） |
| 描述来源 | 变量翻译 432 + 模板 183 | **变量翻译 432 + 模板 183**（不变） |
| 测试基线 | 8 failed / 282 passed | **8 failed / 282 passed**（不变） |

## 版本号 9.19.0 → 9.20.0

## 新工作流用法

```bash
# 预测（可选传入人工表）
python plc_cli.py predict --source 源码.xml --pattern "m_nNextAutoStep[*]" --out outputs/工序 --manual source/工序/人工表.xlsx

# 批次处理后 finish（自动 A档描述 + 交付表 + 自动 coverage）
python plc_cli.py finish --run outputs/工序
```
