# v9 — PLCopen 显式连线局部控制关系

## 新增
- `plcopen_controlflow.py`：针对 PLCopen LD/FBD，通过 `refLocalId` 从数值 MOVE 的 EN 引脚追踪到前级 EQ 比较、触点和布尔功能块，输出 `plcopen_control_relations.csv` 与摘要。
- 仅在沿明确连线发现**同一个目标状态变量**的 EQ 条件且路径无未知节点时，生成 `wired_move_guard_local` 状态候选；未解析的路径单列审计，不能当作已验证。
- 保留 v8 高召回相邻状态假设、事件图和原有过滤；新版本仅在有证据时额外提供强一级的关系。
- 对 AND/OR/NOT 中的 OR、NOT 不做随意等价推理，而是降为不完整证据；未从 EN 连线追溯到状态的不当作因果关系。

## 局限
- 仅支持 PLCopen 标准 LD/FBD 的部分连接写法；厂商扩展、跨POU调用、复杂OR分支和 ST CASE/IF 的精确CFG仍待扩展。
- `wired_move_guard_local` 不证明执行器完成，也不证明 `Step=100 AND PosOK` 与 `Step=110` 时间戳相同。
- 现有环境只有三项目之前上传的候选、事件及人工文件，**没有三份完整原始PLC工程 XML**，因此没有真实三工程 v9 端到端指标。Excel沿用上轮候选基线并明确标注。
- 所有人工参考仅在对照工具中使用，绝不参与候选生成。
