# 本地使用

在解压后的仓库目录运行：
```powershell
python -m pip install -r requirements.txt
python -m unittest discover -v
opencode
```
预测对话：`D:\PLC\source.xml | ST*.AutoStep[*]`
评价对话：`D:\PLC\run\prediction_collection_pairs.csv | D:\PLC\gt.tsv`
需要时在评价对话另行给对应XML；源码哈希不一致会被拒绝。

Python解析和分批准备（此步不调用大模型）：
```powershell
python plc_cli.py predict --source "D:\PLC\source.xml" --pattern "ST*.AutoStep[*]" --out "outputs\run913"
```
准备结束主候选已在 `prediction_collection_pairs.csv`。最终工艺整理仍需OpenCode完成全部批次再finish，不能把准备说成LLM端到端完成。

只读评价：
```powershell
python plc_cli.py evaluate --prediction "outputs\run913\prediction_collection_pairs.csv" --gt "D:\PLC\gt.tsv" --xml "D:\PLC\source.xml" --out "outputs\eval913"
```
`gt_match.xlsx` 分类整行背景色；有条件迁移替代保留参考完整单对和门控，不计严格命中。
Excel导出仍使用既有artifact_tool；该模块缺失时CLI保留CSV/JSON并明确报依赖缺失。

单条只读解释：
```powershell
python feedback_successor.py --graph "outputs\run913\source_transition_graph.json" --start "Cell.Phase=70" --end "Cell.Phase=70 AND Motor.InPlace" --out "outputs\queries\feedback.json"
```
此命令不重新预测，不增改任何候选；输入只用于在既有源码图中查询。
