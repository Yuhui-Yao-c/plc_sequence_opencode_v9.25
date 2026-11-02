# v9.10 复现与验收

## 冻结输入
源码使用用户直接导出的欧姆龙IEC61131-10 XML（本轮没有改写XML）。
SHA-256：`2dae58114ff273f7b9bbd1bf1ab7d0b2b435808ed284186e0be98a757a176447`。
GT 523条，保留重复、复合写法与疑似异常。GT只在预测完成并冻结后用于评价。

## 源码运行
```powershell
python plc_cli.py predict --source "D:\PLC\source.xml" --pattern "m_nNextAutoStep[*]" --out "outputs\run910"
```
产物：全量16262、名称过滤6877、完整对3601；原子对1887（3426条来源已映射、175待核验）。
旧原池文件与v9.9冻结结果逐字节一致，未通过删减或用GT改标签提高结果。

## 独立评价两个视图
```powershell
python plc_cli.py evaluate --prediction "outputs\run910\prediction_candidate_pairs.csv" --gt "D:\PLC\gt.tsv" --out "outputs\eval_raw"
python plc_cli.py evaluate --prediction "outputs\run910\prediction_atomic_pairs.csv" --gt "D:\PLC\gt.tsv" --out "outputs\eval_atomic"
```
第一条是原式候选基线：411/523唯一完整标签形式命中，新增源模型证明0。
第二条是非复合表：10严格单对；370有条件后继；59多分支齐全仅诊断；84其他。
全局等价未证明的后继，不因已知局部源路径就当严格正确。原始复合GT也不允许任选一个分支计分。
GT分支诊断共520个可生成分支，507个能找到完整单行预测；该分母排除了无法展开的原始GT，不能称507/520为完整动作覆盖率。
评价前后冻结预测目录38个文件SHA256一致。

## 使用最终工艺表
predict只是Python解析及分批准备。真实OpenCode需加载skill、逐批分析并保存结构化结果，然后运行：
```powershell
python plc_cli.py finish --run "outputs\run910"
```
所有主final表均为非复合视图，原稿另外归档；未处理批次不会被当成完成。本次没有实际调用外部LLM执行这一步。

## 源码实例（不是无条件计时证明）
- `m_nNextAutoStep[39]=200 → m_nNextAutoStep[39]=205`，T0001863。
- `m_nNextAutoStep[39]=300 → m_nNextAutoStep[39]=305`，T0001871。
- `m_nNextAutoStep[39]=500 → m_nNextAutoStep[39]=510`，T0001878。
- `m_nNextAutoStep[64]=2910 → m_nNextAutoStep[64]=2010`，T0002916。
- `m_nNextAutoStep[64]=2960 → m_nNextAutoStep[64]=2010`，T0002918。
这几对来自源码迁移，普通复位/其他未解析写入仍可能影响集合退出等价，完整条件见源图和atomic_conditions.json。

## 验证
196项unittest通过；新规则含OR跨分支禁止错误配对、内部OR重触发、AND不得丢条件、负向/回零后继、集合首出口、作用域、未知写入、计分隔离和finish主表形式检查。
可用 `python -m unittest discover -v` 复现。
代码不含上述项目特定变量或步号作为规则；具体实例只在本文及回归结果中展示。

OpenCode skill目录与frontmatter依据官方文档： https://opencode.ai/docs/skills/ （核验于2026-10-26）。
PLC业务解释仅依用户源文件及本仓库规则，不以网络材料替代源码。
