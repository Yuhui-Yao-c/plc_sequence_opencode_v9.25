# V9.17 版本变更说明 — 工位身份映射（station_identity）

日期：2026-10-29
基线版本：V9.16（变量文档自动提取 + 交付表工位命名/排序修复）
工程背景：V9.16 修复了顶盖焊交付表质量问题，但方案是"逐个工程加正则前缀"（`udt_EMxx`→`EMxx`），不具备通用性——未来新工程出现 `fbEMxx`/`gvar_EMxx` 等新命名模式时仍需改代码。本版从架构层面解决：**predict 阶段建立工位身份映射，展示层只读不猜**。
测试基线：8 failed / 282 passed（与 V9.16 基线一致，全程未引入新失败）。

## 问题本质
V9.16 之前的架构有一个结构性缺陷：
```
predict 阶段（create）         展示层（gen_workstation_xlsx）
├─ 提取候选（构成要素=POU名）   ├─ 临时 split('.')[0] 算变量根
├─ 边界规范化                  ├─ 临时 _station_code() 猜编号（逐个加正则）
├─ 原子化                      ├─ 临时 workstation() 拼工位名（三级优先级）
└─ 不建立工位身份映射          └─ 无文档时静默用裸名 → 交付表才发现问题
```
工位命名完全是展示层的临时计算，predict 从不建立"状态变量→工位身份"映射，也没有任何工位身份产物。

## 变更清单

### 1. 新增 station_identity.py（predict 阶段工位身份解析）
- 从 `prediction_candidate_pairs.csv` 的"控制器"列提取**实际使用的**变量根（只处理状态机锚点变量，不处理辅助数据结构）。
- 从 `variable_documentation.json` 查文档（stations 子集 + all 全量 fallback）。
- 生成 `station_identity.json`：每个变量根 → `{display, code, doc, sort_key, source, needs_review}`。
- **身份键 = 变量根完整名**，不剥离命名空间前缀（`HJXHX_ST08` ≠ `ST08`）。
- **显示编号剥离**仅限 IEC61131-3 标准类型前缀（`udt_`/`st`/`St`/`ST` 后跟大写字母），命名空间前缀保留。
- **排序键**从变量根最后一个数字组提取 `[主号, 子号]`（`EM07_1`→`[7,1]`）。
- **无文档**→`needs_review=true`，`display=变量根本身`，不静默用裸名。
- 可独立 CLI 运行：`python station_identity.py --run outputs/<run>`。

### 2. plc_cli.py predict 集成（失败不阻断）
- predict 在 var_docs 提取后调用 `station_identity.build_identity()`，生成 `station_identity.json`。
- `candidate_audit.json` 增加 `station_identity` 审计段（total_var_roots / with_documentation / without_documentation / without_documentation_roots）。
- 无文档变量根时 stderr 预警（predict 完成后即可发现，不等交付表）。
- 版本号 9.16.0 → 9.17.0。

### 3. gen_workstation_xlsx.py 优先读 station_identity（fallback 零退化）
- 新增 `load_station_identity(run)` 加载 `station_identity.json`。
- `workstation()` 增加 `si_map` 参数，优先级：
  1. 构成要素 SC04 中文后缀（子工位粒度，用 station_identity 的 code + 子工位名）
  2. station_identity display（工位粒度，如 `EM04_长边清洗工位`）
  3. v9.16 var_docs fallback（station_identity 不存在时）
  4. v9.16 裸 code + section fallback（最后兜底）
- 排序键优先用 station_identity 的 `sort_key`，无映射时用 `_em_sort_key`。
- **v9.16 逻辑完整保留为 fallback**，无 `station_identity.json` 时零退化。

### 4. SKILL 文档（plc-station-identity）
- 新建 `.opencode/skills/plc-station-identity/SKILL.md`。
- 规则：身份键=完整变量根、标准类型前缀剥离、无文档进 review、展示层只读。

## 验证结果

| 工序 | station_identity roots | 有文档 | 无文档 | 工位数 | 无中文 | 与 v9.16 一致 |
|---|---|---|---|---|---|---|
| 顶盖焊 | 22 | 22 | 0 | 56 | 0 | ✓ |
| 超声波 | 18 | 18 | 0 | 61 | 0 | ✓ |

### Fallback 验证（无 station_identity.json）
临时移走 station_identity.json 后重跑：工位数/无中文数/排序**完全一致**（走 v9.16 var_docs 路径），零退化。

### 通用性验证
- `_display_code()` 只剥离 `udt_`/`st`/`St`/`ST` 标准类型前缀，不逐个枚举工程前缀。
- `HJXHX_ST08`（铝焊2）和 `ST08`（滚筒线）身份不塌缩（超声波实际只用 ST01-ST18，HJXHX_STxx 是辅助数据结构不在候选控制器里）。
- 未来 `fbEM05`/`gvar_EM12_3` 等新命名模式：`fb`/`gvar` 不是标准类型前缀 → 保留完整变量根作为身份键，不塌缩。

## 为什么这个方案通用

| 未来场景 | V9.16（逐个加正则） | V9.17（身份映射） |
|---|---|---|
| 新工程变量根 `fbEM05` | 展示层加正则 `fbEM` | station_identity 提取文档，code=`fbEM05`（保留），display=`fbEM05_文档` |
| 新工程变量根无文档 | 交付表裸名，事后发现 | predict 审计报警，station_identity review 可见 |
| 新工程有多个命名空间前缀 | 可能身份塌缩 | 身份键=完整变量根，不塌缩 |
| 新工程 POU 名带中文 | 凑合能用 | station_identity + 构成要素后缀双路径，更精确 |

核心转变：**从"展示层猜名字 + 逐个工程加正则" → "predict 阶段建身份 + 展示层只读 + 无文档进 review"**。
