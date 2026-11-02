# V9.14 版本更新说明 — omron ST 解析增强

日期：2026-10-28
基础版本：V9.13（AGENTS.md 现行路由）
触发背景：配对机工程（omron Sysmac IEC61131-10，工艺以 ST 状态机编写）predict 0 候选。
根因四层：① smcext:StSection 类型未被 ST body 白名单收录（268/397 个 ST 块从未进入解析器）；
② STParser 对函数调用/字符串赋值整块阻断；③ 条件语法不支持变量-变量比较、顺序比较、XOR、
括号比较、进制字面量；④ st_tokens 把紧贴代码的行内注释吞进符号 token。

## 变更清单

### 1. omron_native.py — ST 块入口：枚举 → 结构特征识别 + 未知登记
- ST body 识别改为双轨：`xsi:type` 枚举（ST/STBody/StructuredText/StSection，兼容旧变体）
  **或** BodyContent 存在直接 `<ST>` 子节点（IEC 61131-10 保证的文本载体，未来任何厂商
  命名都能命中）。仅查直接子节点，LdSection/LD 内的 InlineST 不会误入此路径。
- 每个 BodyContent 强制归类进 `native_parse_audit.bodycontent_audit`：
  `parsed_st` / `rung_path`（由 Rung 消费标记判定）/ `skipped_unknown`。
  未读类型显式 warning，不再静默丢弃。
- 取文改为叶子 `<ST>` 文本优先（多 ST 子节点拼接），无 ST 子节点回退 itertext。

### 2. successor_graph.py — STParser 双轨（strict）
- `strict=True`（默认）：Rung 内 InlineST 尾段行为**与 v9.11 完全一致**——不可证语句
  （调用/无参语句/字符串赋值/表达式 RHS/RETURN）阻断整段。尾段合并的零副作用前提不变，
  由既有测试锁定（test_return_blocks_linear_recovery、test_opaque_rhs_blocks_recovery 等）。
- `strict=False`（仅 omron BodyContent 大状态机）：上述语句降级为 `skipped` 记录
  （token 位置/原因/原文 ≤200 字符），body 其余可证写保留：
  - `st_call_may_write_state` / `st_opaque_statement_may_write_state`
  - `st_string_assignment_unmodeled_effects`（步号提示文本 strStepInfo 等）
  - `st_expression_assignment_unmodeled_effects`（bBusy := a OR b 等）
  - RETURN **仍阻断**（其后语句本扫描不可达，提取会伪造线性迁移，v9.11 设计）。
- skipped 计数进 `native_parse_audit.counts.skipped_st_statements`；
  omron parse_st 把 skipped 存入 inline_slices 并写 warning；
  `signal_tail_merge.py` 新增否决理由 `skipped_st_call_may_write_state`——
  含未建模调用的尾段不参与 A→C 合并。
- `case_context` 贯穿 CASE 分支体内嵌套 IF/ELSIF/ELSE 递归（分支标签永不被误当语句）。
- ST_TOKEN 字符类排除 `/`：`AND//注释` 不再把整行注释吞成符号 token；
  除法本就不支持，行为不变差。
- PLCopen 路径（超声波工程）保持 strict=True：已交付的 94.1% 评价结果不受影响。

### 3. boundary_logic.py — 条件语法扩展（全部为"不透明原子"）
新增原子，共同约束：**保留原文、不锚定正状态、satisfiability 记 possible（非证明）**：
- `veq`/`vne`：变量-变量等式/不等式（含 `x = TRUE`）
- `gt/lt/ge/le`：变量 op 整数顺序比较；整数在左时自动交换翻转（`10 > x` → `x<10`）
- `xor`：XOR 节点，**不做布尔化**（按位异或 ≠ 布尔异或），优先级 OR > XOR > AND
- `opaque_cmp`：括号表达式与值比较（`(a XOR b) = 2#1111` 整体保留原文）
- `2#/8#/16#` 进制字面量（`integer()` 正确换基，如 `16#0980` → 2432，可正常锚定）
- negate 映射：veq↔vne、gt↔le、lt↔ge；未识别类型仍走 NOT 包裹默认分支。
- 下游涟漪已核查：comparisons()/substitute()/positive_states() 仅收 eq/ne（新原子自动
  排除，保守正确）；compatible() 未知类型记 possible；fb_transport.required_signals()
  未知类型返回空集；feedback_successor._reduce_true() 原样保留。

### 4. signal_tail_merge.py
- 新增合并否决理由 `skipped_st_call_may_write_state`（见 2）。

## 兼容性与验证
- 全量测试：基线 8 failed / 282 passed（预存失败与本轮无关）→ **每步改动后保持不变**；
  过程中两处回归（RETURN 阻断、opaque RHS 阻断）被 v9.11 测试即时拦截并回退/改为双轨。
- 包mylar（同格式 LD 工程）：bodycontent_audit 全归类（LdSection 444/LD 25/ST 15，
  skipped_unknown=0）；transitions 3216→3632（strict=False 对其 ST/InlineST 的同向增益）。
- 超声波（PLCopen）：路径行为不变。
- 配对机重跑（模式 `stEM*.ProcessCtrl.iCmdStep`）：
  ST bodies 65→333；transitions 2→1944（其中 ProcessCtrl.iCmdStep 663 条，覆盖 28 台 EM）；
  完整候选 0→**1155**；原子对 648；EM100 `0→10` 守卫与源码逐字一致。
  经 141 批语义判定 + finish：事件 5691/5691 全覆盖，steps 663（工艺等待155/检测测量176/
  交接数据126/机械动作57/内部辅助80/未分类69），final_prediction_pairs.csv 交付。

## 已知限制（如实）
- 99 个 ST 块仍解析失败：72 个 FOR 循环（方案外未动）、7 个空括号条件、其余零星
  （缺 THEN、变量-变量顺序比较等）——全部在 blockers/audit 可见，未静默。
- skipped 语句（约 2.9 万条次，含 strStepInfo 文本、调用、表达式赋值）效果未建模，
  仅作审计记录；不参与状态锚定。
- cross_fb 适配器仍仅支持 PLCopen LD/FBD；omron ST 工程跨 FB 输送候选为 0 属预期。
- signal_merge proposals=0 属预期（本工程工艺在 Body 级 ST 状态机，非 LD+InlineST 尾段结构）。

### 5. 交付链：finish 自动生成工位步序交付表
- gen_workstation_xlsx.py 暴露 generate()（返回统计字典）；workflow.validate(write=True)
  在成功组装后自动输出 <工序名>_步序采集表.xlsx（工序名从运行目录名推断去 _vN 后缀）；
  分组auto（构成要素含SC04命名→构成要素，否则有证据POU列→证据POU）；
  工序名/来源/行数/分组/SHA256与"排序仅为展示"声明写入表头注记。
- 依赖缺失或生成失败仅 stderr 警告并记入 workflow_status.json，CSV 交付物不受影响；
  手动重渲染：python gen_workstation_xlsx.py --prediction ... [--group-by auto|构成要素|证据POU|控制器]。

