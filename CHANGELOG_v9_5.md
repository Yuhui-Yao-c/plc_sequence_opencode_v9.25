# v9.5 — 从状态EQ向下游寻找有连接证据的工艺事件

- 新增 `plcopen_action_trace.py`：流式解析LD/FBD，构建refLocalId边，从状态EQ沿下游BFS搜索动作线圈、检测/等待相关块、定时器和MOVE；保留XML节点路径；排除孤立元件；限制深度。
- `agent.py`预测流程自动输出 `plcopen_action_trace.csv`、`plcopen_action_trace_summary.json`；`workflow_manifest.json`包含新增trace统计。
- 禁止把连线可达性认定为真值等价或物理完成；无真实工艺动作证据的步号邻接仍保留在回查池，不自动升级成最终动作。
- 新增2项连线搜索回归测试，全库56项测试通过。
- 预测/评价严格分离、候选一对一评价与整行彩色Excel保留自v9.3。
- 未取得三工程完整原始XML，本轮不声明三项目新召回率。
