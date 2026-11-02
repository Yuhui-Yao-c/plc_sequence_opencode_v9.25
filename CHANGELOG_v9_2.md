# v9.2：预测与客观评估隔离

- 单入口 `plc_cli.py`：`predict` / `evaluate` / `finish` 三条命令，分别对应两个功能和预测组装步骤。
- 预测不接受GT，也不读取GT；GT仅在evaluate中加载，永不反向影响候选。
- OpenCode AGENTS.md 按两种输入路由：`XML | 通配符` -> 预测；`预测CSV | GT` -> 评价。
- 评价指标只针对提供的预测文件；支持候选表与 `final_sequence.csv`，不能混用两者的匹配数。
- 预测、评价记录SHA256；评价导出逐条GT/TopK候选及xlsx。
- 核心静态解析算法沿用v9.1；本版解决任务路由、重复性和防GT泄漏，不声称三项目真实动作召回率提高。
