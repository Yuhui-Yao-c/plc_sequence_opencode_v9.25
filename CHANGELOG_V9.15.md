# V9.15 版本变更说明 — 交付表命名固化 + 动作描述生成（A/B 档）

日期：2026-10-28
基线版本：V9.14（AGENTS.md 已加路由节）
工程背景：超声波（PLCopen）与配对机（omron Sysmac）两工程全流程已交付；本版聚焦**最终交付物（工位步序采集表）的命名固化、自动生成与动作描述升级**。
测试基线：8 failed / 282 passed（与 V9.14 基线一致，预存失败与本版无关，全程未引入新失败）。

## 变更清单

### 1. 交付表命名固化（gen_workstation_xlsx.py）
- 交付文件固定命名 **`<工序名>_步序采集表.xlsx`**，缺省输出到 prediction 同目录；`--out` 从 required 降为可选。
- 新增 `--proc-name`；缺省从 prediction 目录名推断并剥离 `_vN` 后缀（`配对机_v2`→`配对机`），推断值打印可核对。
- 表内同步对齐交付语义：标题行 `<工序名> 步序采集表（预测交付）`，说明行增加 `工序:` 字段，sheet 名默认 `步序采集表`。
- AGENTS.md 调用示例同步更新。

### 2. finish 自动生成工位表：接线验证 + auto 分组修复
- `workflow.py` finish 成功后自动调用 `gen_workstation_xlsx.generate()`（此前接线已写但从未被真实 run 执行）；本版对超声波/配对机两 run **端到端重跑 finish 验证**，`workflow_status.json` 的 `workstation_xlsx` 记录（工序名/组数/行数/SHA16）落地。
- `pick_group_by()` auto 判定修复：原逻辑要求构成要素**字面含 `SC04`**，导致 omron 工程（构成要素为 `stEM310.ProcessCtrl.iCmdStep` 类结构体路径）被误判到"证据POU"（仅 4 组粗粒度）。修复为：**构成要素有实际区段命名（非空占比 ≥50%）即用构成要素**；仅当构成要素缺失/全空且有证据POU列时才用证据POU。AGENTS.md 措辞同步。

### 3. 中文工位名落地（承接 V9.14 会话尾部，本版验证收口）
- 新工具 `extract_variable_docs.py`：从 PLCopen/omron 双格式源码提取变量级 documentation（流式 iterparse、全局/局部作用域合并、`sStationConrol` 工位结构体单列），输出 run 目录 `variable_documentation.json`（超声波 69 stations/7847 all；配对机 1867）。
- `clean_doc()` 修复：`<GrpComment_zh-cn>` 提取 + `<Sub/>` 后监控 JSON 截断（HJXHX_ST04/ST05 等嵌套文档清理完整）。
- 工位名优先级：开始标签变量根的源码文档（ST13→A贴胶工位）→ 构成要素中带中文的工艺区段 → 区段原名。两交付表工位组 **100% 中文**（SC04_Run 残留归零）。

### 4. 动作描述生成（新模块 `action_describe.py` + 词典 `action_wordbook.json`）
交付表"步序"列从动作变量列表升级为**动作描述**（方案论证后按 A+B 档落地）：

- **A 档（源码事实模板，零 LLM）**：机构词（变量文档四级匹配：精确 token→数组根→基名→前缀，文档缺失用 roots 兜底词典）+ 动作词（成员词典，含 omron 两级成员链 `Pick.bCmd_A`→"取料·动作命令A"，modules 词典 20 项、members 58 项、member_patterns 正则 6 条）+ 纯迁移行标"步进保持"。两工程 614+648 全行覆盖。
- **B 档（LLM 工艺语义，批次机制）**：`build` 生成 `batches_desc/*.input.json`（含 A 档句、tokens 文档、prev/next 跨行上下文、白名单规则）→ OpenCode/agent 逐批写 `*.result.json` → `validate` 白名单校验合并。
- **白名单校验器（防编造三闸）**：① refs ⊆ 该行 tokens；② 纯 ASCII 标识符（≥3 字符或含 `.`/`[`）必须在 tok/机构词/A 档描述中出现过，子串放行（`CCD`⊂`CCD1`）；③ 中文连续串必须命中动词白名单/成员词/该行机构词。失败进 `action_desc_review.csv` 并 A 档兜底。
- **词典纪律修正**：前缀回退收紧（键基串 ≥6 字符，防 `bEM` 短前缀吞语义）；`CallPut/PickOK/PutOK` 信号名细分（呼叫放置/拾取完成/放置完成，member_patterns 正则）；受影响 31 行**主动降级** A 档兜底并记入 review。

### 5. 交付表结构升级（generate 集成 desc_map）
- 列结构：**工位 | 动作描述 | 动作变量（引用） | 开始标签 | 结束标签**（5 列）；`run/action_desc_final.csv` 存在时 col C 自动升级为动作描述，col D 为完整动作变量引用（不再截断，步进行引用列留空）。
- 说明行记录 B/A 档条数比例 + SHA256；`workflow_status.json` 的 `workstation_xlsx` 新增 `b_desc_rows` 与 `desc_sources`。
- 终验：超声波 614/614、配对机 648/648 中文动作描述 100%，标签对与 final CSV 逐行一致；配对机 B 档 475 + A 档 173（review 31 条为词典修正主动降级）；超声波 B 档 429 + A 档 185（review 2 条为真实越界拦截）。

### 6. 交付表格式对齐现场模板（追加变更）
- 以 `source\转接片\4.厦门2转接片 22PPM转接片焊接机步序采集表-20260905步序拆解(已自动还原).xls` 的"时序拆解"sheet 为模板重构渲染：
  - 列结构 12 列：**构成要素 | 动作 | 开始适用(s) | 动作时间(s) | 完成时间(s) | 动作描述 | 时长(ms) | 目标时长(ms) | 动作变量（引用） | 开始标签 | 结束标签 | 备注**；
  - 现场分块习惯：工位块首行写名并**纵向合并至块尾**，块尾加 **"总时长"行**（时长列预测侧不填），块间空行；
  - 时间列（开始适用/动作时间/完成时间/时长/目标时长）为现场实测项，**表头保留、值留空**；"动作"列（源码动作分解）与"备注"列同样留空待填；
  - 内容仍全部来自预测产物：动作描述（A/B 档）、动作变量引用、开始/结束标签。
- 终验：两表块尾=总时长 28/28、32/32；时间列全空；标签对与 final CSV 逐行一致；中文描述 100%。
- 评价侧不受影响（评价仍直接读 CSV）。

## 证据边界（不因升级而模糊）
- 动作描述的工艺语义（取料/放料类）是**保守归纳的推断层**：每条带源码引用、过白名单校验，但不构成对 GT 语义的证明；方向符号（↓/↑/→）静态不可判定，按论证结论不输出。
- review 文件（`action_desc_review.csv`）如实保留：超声波 2 条（行外词/编造 token 拦截）、配对机 31 条（词典修正主动降级）。
- 预测隔离红线不变：动作描述生成不读 GT/人工表；人工表仅在评价侧对照。

## 新增/修改文件
- 新增：`action_describe.py`、`action_wordbook.json`、`extract_variable_docs.py`、`CHANGELOG_V9.15.md`、`PROJECT_SNAPSHOT_V9.15.txt`
- 修改：`gen_workstation_xlsx.py`（命名固化 + desc_map 集成 + pick_group_by 修复 + clean_doc 修复）、`workflow.py`（版本号）、`plc_cli.py`（版本号）、`AGENTS.md`（标题 v9.15 + 交付表/auto 分组/动作描述路由节）
- 运行产物（不入快照）：`outputs/<run>/variable_documentation.json`、`action_desc_a.csv`、`batches_desc/`、`action_desc_final.csv`、`action_desc_review.csv`、`<工序名>_步序采集表.xlsx`
