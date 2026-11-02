# PLC Sequence Agent v9.25 — Web 控制台使用说明

## 1. 运行方式

Web 控制台采用本机浏览器 + Flask 本地服务 + OpenCode CLI 的结构。浏览器不会直接连接模型服务，也不会自行生成步序结果。

在项目根目录打开终端，执行：

```bash
python -m pip install -r requirements.txt
```

确认 OpenCode CLI 已安装并完成模型提供方认证：

```bash
opencode --version
opencode auth list
opencode agent list
```

项目内应能看到以下两个批次 Agent：

- `plc-process-batch`：逐批处理 `batches/*.input.json` 工艺分析任务。
- `plc-action-description`：逐批处理 `batches_desc/*.input.json` B 档动作描述任务。

启动 Web 服务：

```bash
python web_app.py
```

Windows 也可以双击 `run_web.bat`。默认浏览器地址：

```text
http://127.0.0.1:5173
```

服务默认只监听 `127.0.0.1`，不会自动对局域网开放。

## 2. 一次完整运行
1. 选择 PLC 源码文件（`.xml`、`.smc2` 或 `.txt`）。
2. 设置变量匹配模式。默认 `*`；也可使用 glob 或 `re:` 正则。
3. 选择 `outputs/` 下的新运行目录。每次运行使用独立目录，不覆盖历史结果。
4. 可选 Trace 文件用于运行时跳变分析；可选人工采集表只用于预测完成后的覆盖评价。
5. 点击“开始完整分析”。

后台按以下顺序执行：

```text
Python: plc_cli.py predict
    ↓
OpenCode: plc-process-batch 逐个处理 batches/*.input.json
    ↓
OpenCode: plc-action-description 逐个处理 batches_desc/*.input.json
    ↓
Python: plc_cli.py finish --run <本次运行目录>
    ↓
结果校验、动作描述合并、工艺契约检查、Excel 交付
```

OpenCode 每次只接收一个批次文件，使用项目目录中的 Agent 配置与 Skills。有效的既有 `result.json` 会跳过；缺失或结构无效的结果会重新处理。每个新结果写入原批次对应的 `result.json`。

## 3. 中断与恢复

如果任务中断或某个批次失败：

1. 在历史记录中打开对应运行。
2. 查看“Agent 批次”页签，确认哪些批次通过结构检查。
3. 点击“继续未完成批次”。

恢复时会保留通过检查的结果，只处理缺失或结构无效的批次，然后重新执行 finish。不会重新运行 predict，也不会覆盖有效批次结果。

## 4. 运行环境配置

如果 OpenCode CLI 不在系统 PATH，可设置环境变量 `OPENCODE_BIN` 指向 CLI 可执行文件。

可选配置：

- `PLC_WEB_PORT`：Web 服务端口，默认 `5173`。
- `PLC_WEB_HOST`：监听地址，默认 `127.0.0.1`。
- `PLC_WEB_NO_BROWSER=1`：启动时不自动打开浏览器。
- `PLC_OPENCODE_BATCH_TIMEOUT_SECONDS`：单个 OpenCode 批次超时，默认 `900` 秒。

不要将服务监听地址改为 `0.0.0.0`，除非已配置可信网络访问控制。当前版本面向本机使用，不包含用户登录与多用户权限系统。

## 5. 重要的数据边界

- predict 不读取 GT。
- 工艺 Agent 只处理本批候选与事件，并必须让每个事件进入 `steps` 或 `unresolved_events`。
- B 档动作描述 Agent 只使用本批 token、源码步注释与相邻行上下文。
- 人工采集表只传入 predict 的 `--manual` 参数，由 finish 阶段触发覆盖评价；不进入 Agent 批次上下文。
- Web 后端会检查批次 ID、事件覆盖、候选引用及 B 档行覆盖。最终的完整校验仍由项目原有 `workflow.validate()` 和 `action_describe.cmd_validate()` 执行。
- Web 控制台不会向 PLC 写入数据，也不会修改 PLC 源程序。


## 6. OpenCode 批次权限边界

Web 调度器通过 `opencode run` 调用项目内两个单批 Agent。它们的权限按批次工作所需收敛：

- 允许读取项目内证据、读取 Skills、检索文件。
- 只允许写入项目 `outputs/` 下本次运行目录中的结果文件。
- 禁止 Bash、子 Agent、外部目录访问和 Web 搜索/抓取；同时拒绝读取 `.xls`、`.xlsx`、`.xlsm` 文件，避免人工基准表进入 Agent 上下文。
- OpenCode 的 `--auto` 只自动批准未被显式拒绝的权限；上述明确拒绝项不会被自动批准。

因此，单批 Agent 不能自行重跑 predict/finish，也不能修改 PLC 源码或项目代码。最终 finish 由 Flask 后端执行一次。
