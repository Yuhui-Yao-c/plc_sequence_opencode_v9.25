# v9.6 两种输入

预测：`D:\PLC\source.xml | ST*.AutoStep[*]`。批处理完成前可直接查看 `prediction_candidate_pairs.csv`，完成后查看 `final_prediction_pairs.csv`；工艺精选另见 `final_process_steps.csv`。

评价：`D:\PLC\outputs\run1\final_prediction_pairs.csv | D:\PLC\gt.tsv`。如另提供同一原始PLCopen XML，Agent可使用 `--xml` 验证源代码迁移的边界替代，仅在评价中读取XML。

```powershell
python plc_cli.py predict --source "D:\PLC\source.xml" --pattern "ST*.AutoStep[*]" --out outputs\run1
python plc_cli.py finish --run outputs\run1
python plc_cli.py evaluate --prediction outputs\run1\prediction_candidate_pairs.csv --gt "D:\PLC\gt.tsv" --xml "D:\PLC\source.xml" --out outputs\evaluation1
```

**不要用 `final_process_steps.csv` 来代替全量候选召回评价**；这是工艺精选。
