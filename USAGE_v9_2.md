# v9.2 本地使用

## 预测：PLC XML + 通配模式

启动仓库根目录的 `opencode`，输入：

`D:\PLC\project.xml | ST*.AutoStep[*]`

Agent依AGENTS.md执行 `plc_cli.py predict`，逐批整理后调用 `finish`。最终草表 `final_sequence.csv`；用于高召回验收也可使用 `sequence_candidates_filtered.csv`。二者不能混称。

独立终端命令：

`python plc_cli.py predict --source "D:\PLC\project.xml" --pattern "ST*.AutoStep[*]" --out "outputs\run001"`

仅准备数据；若不运行OpenCode批次，`final_sequence.csv` 不会出现。

## 评价：预测结果 + GT

在OpenCode输入：

`D:\PLC\outputs\run001\final_sequence.csv | D:\PLC\gt.tsv`

独立终端命令：

`python plc_cli.py evaluate --prediction "D:\PLC\outputs\run001\final_sequence.csv" --gt "D:\PLC\gt.tsv" --out "outputs\evaluations\run001"`

可选 `--relations "D:\PLC\outputs\run001\plcopen_control_relations.csv"`，仅接受事先生成的证据旁路文件。输出GT逐条CSV、TopK详情和 Excel。

如需评价**全量候选召回**，把 `--prediction` 改为 `sequence_candidates_filtered.csv`；如需评价**整理后草表质量**，使用 `final_sequence.csv`。两者分开出报告。

注意：`gt_search.py` 的条件迁移类属于**未验证时间戳等价**，不应直接并入严格正确率。CSV/TSV表头应有 `开始标签` 和 `结束标签`；目前GT工作簿请另存为CSV/TSV再运行。
