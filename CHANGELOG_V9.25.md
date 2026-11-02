# CHANGELOG V9.25 — 动作描述质量增强：变量文档注入 + 简短动作重点 + 缩进修复

## 核心改动

### 1. 缩进 bug 修复（plc_cli.py）
**问题**：`extract_network_comments` 和 `extract_step_comments` 的 try/except 块被错误缩进嵌套在 `extract_variable_docs` 的 except 块内。当 `extract_variable_docs` 成功时（不抛异常），except 块不执行 → `step_comments.json` 从不生成 → A 档描述全空。

**修复**：三个 try/except 块改为平级独立，各自独立执行。

### 2. 变量文档注入（action_describe.py describe_token）
**问题**：`describe_token` 对 `EM01V.Cy_Up1.In.Auto_R` 只返回工位名 `EM01_顶盖上料模组`，不查 `Cy_Up1` 的变量文档。B 档 agent 拿不到"上料平移顶升气缸"等具体变量文档。

**修复**：在处理 rest（成员链）时，提取具体变量名（如 `Cy_Up1`、`Axis_Z1`）查 `variable_documentation.json`（6611 条），将变量文档注入 word 字段：
- `Cy_Up1` → `上料平移顶升气缸`（原 `EM01_顶盖上料模组`）
- `Cy_Location1` → `支架固定气缸1`（原 `EM01_顶盖上料模组`）
- `Axis_Z1` → `SV44_支架上料Z轴`（原 `EM01_顶盖上料模组`）
- `LinkOut_Request1` → `设备To物流线：请求上料`（原 `EM01_顶盖上料模组`）

### 3. member_patterns 扩展（action_wordbook.json）
新增/修改模式：
- `.*Cy_.*\.Auto_R` → `气缸复位`（原"气缸自动动作"）
- `.*Cy_.*\.Auto_V` → `气缸动作`（原"气缸自动动作"）
- `.*Axis.*\.Stop_Auto` → `伺服停止`（新增）
- `.*Axis.*\.JOG_Auto.*` → `伺服点动`（新增）
- `.*Axis.*\.Home_Auto` → `伺服回零`（新增）
- `.*Mo_.*\.StartRight` → `电机正转启动`（新增）
- `.*Mo_.*\.StartLeft` → `电机反转启动`（新增）
- `.*LinkOut_Request.*` → `送出请求信号`（新增）
- `.*Station\[N\]\.User_Alarm\[N\]` → `工位报警`（新增）

### 4. 简短动作重点（action_describe.py cmd_build + cmd_validate）
**问题**：B 档 agent 把一行所有 token 的 word+action 全部拼接，产生 54 字符超长描述（"顶盖堆栈出料横移调速电机进料侧电机正转启动顶盖堆栈进料横移调速电机出料侧电机正转启动"）。

**修复**：
- task 指令改为"一行只描述一个主要动作重点，不要把所有tokens拼接到一起"
- 明确优先级"伺服定位>气缸动作>信号，忽略次要信号和报警"
- `max_len` 60→25
- `cmd_validate` 超长阈值 80→30

### 5. cmd_build 注入步注释（action_describe.py）
- 每行增加 `step_comment` 字段（当前行 start/end 标签对应的源码步注释）
- prev/next 增加 `sc` 字段（相邻行的步注释）

### 6. B 档状态如实报告（workflow.py）
finish 报告 `b_tier_input_count`/`b_tier_result_count`/`b_tier_pending`，未处理时 stderr 报警。

## 文件变更

| 文件 | 变更 |
|---|---|
| `plc_cli.py` | 修复 extract_network_comments/extract_step_comments 缩进；版本 9.24.0→9.25.0 |
| `action_describe.py` | describe_token 注入变量文档；cmd_build 注入 step_comment+sc；task 指令+max_len+超长阈值 |
| `action_wordbook.json` | member_patterns 扩展（Cy_*.Auto_R、Axis.Stop_Auto、Mo_.StartRight 等） |
| `workflow.py` | B 档状态如实报告；版本 9.24→9.25 |
| `AGENTS.md` | 新增 v9.25 路由节 |

## 验证结果（转接片）

| 指标 | v9.24 | v9.25 |
|---|---|---|
| A 档有描述 | 0（缩进 bug） | 337 |
| B 档有描述 | 1488 | 1151 |
| 唯一描述 | 46 | 264 |
| 超长(>30字符) | 无数据 | 9（全是 A 档步注释原文） |
| B 档拼接超长 | 无数据 | 0 |
| 描述样本 | "伺服定位"(453次) | "请求上料信号""支架上料Z轴伺服定位""支架固定气缸复位" |
| 测试基线 | 8 failed / 282 passed | 8 failed / 282 passed（无回归） |

## 工作流一致性
所有工序走同一条路径，无工序专属逻辑：
```
predict
  ├─ extract_variable_docs / network_comments / step_comments（三个独立 try）
  ├─ cmd_a（读 collection，describe_token 注入变量文档）
  └─ cmd_build（新 task 指令 + max_len=25 + step_comment）

agent 处理 B 档批次（所有工序相同指令）

finish
  └─ cmd_validate（超长阈值 30，所有工序相同校验）
```
