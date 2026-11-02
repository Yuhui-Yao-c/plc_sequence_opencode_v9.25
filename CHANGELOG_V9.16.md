# V9.16 版本变更说明 — 变量文档自动提取 + 交付表工位命名/排序修复

日期：2026-10-29
基线版本：V9.15（AGENTS.md v9.15 路由节）
工程背景：顶盖焊（omron Sysmac，98MB）交付表质量差——177 行工位名裸 `SC04_Run` 无中文、步序混排；超声波（PLCopen，89MB）质量可接受。本版针对**工位命名、编号提取、排序**三个根因修复，同时补全 predict 阶段变量文档自动提取。
测试基线：8 failed / 282 passed（与 V9.15 基线一致，预存失败与本版无关，全程未引入新失败）。

## 根因归纳（对照人工表 source\顶盖焊\步序时长采集-XM2-L5-顶盖焊-20261012.xlsx）

| 根因 | 顶盖焊现象 | 超声波为何不受影响 |
|---|---|---|
| **R1：variable_documentation.json 缺失** | predict 从未调用 extract_variable_docs.py；13 个 EM（177 行）构成要素是裸 `SC04_Run` 无中文 | 构成要素自带中文后缀（`SC04_RUN_铜上盘`），不依赖 var_docs |
| **R2：_station_code() 不识别 udt_EMxx** | 工位名带 `udt_` 前缀（`udt_EM20_内侧翻转z`） | 变量根是 `STxx`，恰好原样返回符合预期 |
| **R3：排序键工位名塌缩** | 13 个 EM 塌缩成 1 个 `SC04_Run` 块，12 个 EM 步号混排 | 60 个工位都有中文独立块，排序自然有序 |

## 变更清单

### 1. predict 自动提取变量文档（extract_variable_docs.py + plc_cli.py）
- `extract_variable_docs.py` 核心逻辑提取为可 import 的 `extract(source, out)` 函数；`main()` 调用它（CLI 行为不变）。
- `plc_cli.py predict` 在 `create()` 之后自动调用 `extract_variable_docs.extract()`，生成 run 目录 `variable_documentation.json`；**失败不阻断预测**（try/except，仅 stderr 警告），因为这是展示增强而非预测必需。
- 版本号 9.15.0 → 9.16.0；CLI description 同步。
- 对已有 run（顶盖焊/超声波）手动补生成 variable_documentation.json 验证效果。

### 2. _station_code() 扩展 udt_EMxx 识别 + NaN 防泄漏（gen_workstation_xlsx.py）
- 新增正则分支 `^udt_(EM\d+(?:_\d+)?)$ → EM\d+`，剥离 `udt_` 前缀（如 `udt_EM07_1` → `EM07_1`），对齐人工表编号格式。
- **NaN 防泄漏**：pandas 把 None 转成 float NaN（truthy），`str(NaN)='nan'` 曾泄漏到工位名前缀（`nan_循环线上片`）。修复：`_station_code()` 对 `'nan'/'none'/空` 返回 `''`；`workstation()` 入口用 `pd.notna()` 守卫；`generate()` 的 `_变量根` 计算用 `pd.notna(v) and v` 代替 `if v`。

### 3. workstation() 优先级调整：子工位粒度优先（gen_workstation_xlsx.py）
- **原优先级**：var_docs（工位粒度，如 `ST06_铜侧换盘`）> 构成要素中文后缀（子工位粒度，如 `ST06_铜上盘`）。导致超声波补 var_docs 后子工位被合并（61→33 工位），丢失铜上盘/铜下盘/铜盘移栽区分。
- **新优先级**：构成要素 SC04 中文后缀（子工位粒度，最细）> var_docs（工位粒度）> 非中文后缀兜底 > 裸 code 分组。这样：
  - 超声波 ST06：构成要素 `SC04_RUN_铜上盘` 有中文 → `ST06_铜上盘`（保持子工位粒度）
  - 顶盖焊 EM04：构成要素裸 `SC04_Run` 无中文 → var_docs 命中 → `EM04_长边清洗工位`
  - 顶盖焊 EM19：构成要素 `SC04_Run1` 后缀 `1` 非中文 → 不阻断 var_docs → `EM19_下料机器人工位`

### 4. 裸 SC04_Run fallback 按变量根分组（gen_workstation_xlsx.py）
- 当构成要素无中文、var_docs 也无命中时，用 `_station_code(变量根)` 作为编号前缀（如 `EM04_SC04_Run`），保证**每个 EM 是独立工位块**，不再 12 个 EM 塌缩成 1 个混排块。
- 这是 S1（var_docs）失效时的兜底，保证排序不崩。

### 5. 排序键 EM/ST 编号数值提取（gen_workstation_xlsx.py）
- 新增 `_em_sort_key(name)`：从工位名提取 `(EM|ST)(\d+)(?:_(\d+))?` 数值键，使 `EM01 < EM02 < … < EM10`（而非字符串序 `EM10 < EM2`）和 `EM07_1 < EM07_2`。
- `generate()` 排序键从 `['_工位', '_变量', '_序']` 改为 `['_工位序', '_变量', '_序']`；`groupby(sort=False)` 保持已排序顺序。

## 验证结果

| 工序 | 改前工位数 | 改前无中文 | 改后工位数 | 改后无中文 | 改善 |
|---|---|---|---|---|---|
| 顶盖焊 | 46 | 3（177 行） | 56 | 0 | 全中文，EM01→EM20 数值排序，子工位粒度保留 |
| 超声波 | 61 | 1（9 行） | 61 | 0 | ST02 获中文名 `二次定位`，子工位粒度不退化，`nan_` 前缀消除 |

- 顶盖焊工位名对照人工表：EM01_长边上料工位→EM01_托盘上料Y/磁驱上料Y（子工位）、EM04_长边清洗工位、EM19_下料机器人工位等，全部匹配。
- 超声波工位名保持：ST06_铜上盘/铜下盘/铜盘移栽（子工位粒度未合并）、ST02_二次定位（var_docs 补全）。
- 测试基线 8 failed / 282 passed 不变。
