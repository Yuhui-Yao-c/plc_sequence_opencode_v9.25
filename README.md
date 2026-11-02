# v9.13 到位反馈→逻辑后继（2026-10-26）

本版重点补充：现有预测是状态对，人工或工艺草表是“状态 AND 到位”时，如何找到源码支持的直接后继。
它不按名字猜反馈，不按步号顺序猜目标；不会把正常运行条件下的替代直接标成严格时间等价。

**主预测与评价用法不变：** 在OpenCode输入 `源码路径 | 变量模式` 预测；输入 `预测CSV路径 | GT路径` 只读评价，可附XML。

- 安装：`python -m pip install -r requirements.txt`
- 测试：`python -m unittest discover -v`
- 启动：仓库目录运行 `opencode`。
- 主表：`prediction_collection_pairs.csv`；全量存档：`prediction_candidate_pairs.csv`。
- 单条只读查询：见 `USAGE_v9_13.md`；新增Skill：`.opencode/skills/plc-feedback-successor/SKILL.md`。
- 评价Excel继续整行分类着色；蓝色“有条件的迁移替代”不是绿色严格命中。
- 不为目标数量删主候选，不向PLC写入任何内容，不让GT回流预测。

本版变更及验证见 `CHANGELOG_v9_13.md` 和 `RELEASE_v9_13.md`。下面保留旧版详细架构说明。

---

# PLC Sequence Agent v9.12 — 跨功能块触发与接收阶段

在v9.11上增加PLCopen功能块实例参数关联，修复端口内嵌MOVE目标读取，保留BOOL触发起点。
预测不接受GT；先全量解析与关联，再按名称模式过滤，再做非复合视图和工艺筛选。完整候选不删。

## 本地使用
```powershell
python -m pip install -r requirements.txt
python -m unittest discover -v
opencode
```
OpenCode预测输入：`D:\PLC\source.xml | ST*.AutoStep[*]`。
OpenCode评价输入：`D:\PLC\run\prediction_collection_pairs.csv | D:\PLC\gt.tsv`。
源码内容可存为.txt。glob方括号是数组下标字面字符；`re:`前缀支持变量名全匹配正则。
只要开始或结束任一侧命中模式就保留，信号起点不需要同时匹配状态变量模式。

命令行：
```powershell
python plc_cli.py predict --source "D:\PLC\source.xml" --pattern "ST*.AutoStep[*]" --out "outputs\run912"
python plc_cli.py evaluate --prediction "outputs\run912\prediction_collection_pairs.csv" --gt "D:\PLC\gt.tsv" --xml "D:\PLC\source.xml" --out "outputs\eval912"
```
predict只完成确定性解析和OpenCode批次准备。OpenCode应继续处理全部批次，再执行：
```powershell
python plc_cli.py finish --run "outputs\run912"
```
没有运行真实LLM批次时，不能宣称最终工艺精选已完成。GT建议放在独立目录/独立会话。

## 主要文件
| 文件 | 含义 |
|---|---|
| prediction_collection_pairs.csv | 主采集候选视图：非复合端点，支持BOOL→状态，应用动作后收尾合并 |
| prediction_candidate_pairs.csv | 完整候选对，含证据与不确定性，不受LLM删减 |
| prediction_atomic_pairs.csv | 不合并的原子视图及原始分段 |
| cross_fb_transport_candidates.csv | 从源代码实例绑定得到的完整输送/交接候选，未按名称过滤 |
| cross_fb_transport_links.csv | 发车标志、命令、目的到达、接收状态和行号 |
| cross_fb_transport_evidence.json | FB生命周期、每个实例实参、完整接收门控与限制 |
| cross_fb_transport_review.csv | 未识别/有旁路/未知块/非首接收阶段的原因 |
| source_transition_edges.csv | 局部完整门控支持的状态迁移（不是全局物理证明） |
| signal_merge_lineage.json | 继续保留v9.11动作后数据/信号收尾规则 |

## 本轮修复原理
- PLCopen数据输出可能在`outputVariables/variable/connectionPointOut/expression`，参数名可以为空。
  新版恢复读取，输入操作数按位置保留，不把空参数名放入字典后互相覆盖。
- 跨FB模块从定义中发现“标志与命令同控制点置位、目的到达参与复位”的协议；按每个调用实例的真实实参绑定。
- 接收侧要求正向到达是MOVE门控的必要项，不能因在同一网络出现就关联。后续加工阶段再次使用到达位不继续当输送终点。
- 首次接收存在多个出口时每条分支独立保存，不选最小步号、不硬编码焊接50、不将旁路自动认作正常完成。
- 简单BOOL信号也是非复合端点；不能为了全为步号而删除或替换它。
- 复杂共享OR前缀有展开预算，超限留缺口与候选，不把截断条件伪装为完整。

## 必须理解的边界
结构化协议关联是源码证据，不是触发→物理到达的运行证明。
`发车标志→接收状态`包含到达后其他条件等待，不能直接称纯磁驱运动耗时。
采集器应按本次上升沿锁存，异常取消，多动子时核对ID。标志在到达时复位不代表采集区间已经结束。
本轮新增自动协议提取只支持PLCopen LD/FBD；原生欧姆龙/SMC2保持旧能力，不假称跨FB模块已全格式覆盖。
不支持的FB定义、动态参数、未知语义、局部变量歧义、跨任务/实际扫描和全程序可达性仍需审查。

## Excel评价
评估输出`gt_match.xlsx`，不同类别整行着色；相同预测标签对只能被一条GT计分。
Excel仍使用既有artifact_tool导出器，安装基础requirements仅含解析依赖；本地缺少Excel组件会提示，CSV/JSON照常生成。
本次随仓库另提供已经生成的专项Excel，不使用示例标签反向生成预测。

新增规则见 `.opencode/skills/plc-cross-fb-transport/SKILL.md`；验证见 `RELEASE_v9_12.md`。
