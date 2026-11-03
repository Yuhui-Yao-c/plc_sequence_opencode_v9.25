# PLC Sequence Agent v9.25 — Web 原生 OpenCode 工作台

## 设计目标

**保持与本地 `opencode` 交互式执行尽可能一致。** 当前默认使用项目原有主 Agent `plc-sequence`（由 `opencode.json` 的 `default_agent` 指定）。Web 只提交原本的四段输入：XML 文件路径、变量模式、可选 Trace 路径、可选人工表路径。

Flask 额外传达指定输出目录，以便历史运行、日志、结果下载与恢复。不会新增第二套工作流，不会强制每个批次通过专用 Task 子 Agent，也不会直接调用 `plc_cli.py predict` 或 `finish`。Python 处理仍由原有 OpenCode Agent 按 AGENTS.md / Skills 调度。

## 安装与运行

```bash
python -m pip install -r requirements.txt
opencode --version
opencode auth list
opencode agent list
python web_app.py
```

本机打开 `http://127.0.0.1:5173`；Windows 可双击 `run_web.bat`。系统状态应显示 `plc-sequence` 可用。

OpenCode 使用项目根目录作为 `--dir`，默认如下运行：

```bash
opencode run --agent plc-sequence --auto --dir "<项目目录>" "<XML地址> <变量模式> <可选trace> <可选人工表>\n本次运行的输出目录：<outputs/运行名>。请使用项目原有工作流完成交付。"
```

实际通过 Python `subprocess.Popen(argv)` 传入参数，不会通过 shell 拼接输入字符串；Windows 平台的 .cmd shim 例外由操作系统命令行转换处理。源码路径包含空格时会在模型输入中加引号。

## 对齐本地模型

本地 TUI 可能已手动选定模型，而 `opencode run` 新会话可能使用默认模型。如需强制一致，设置环境变量：

- PowerShell: `$env:PLC_OPENCODE_MODEL="provider/model"`
- CMD: `set PLC_OPENCODE_MODEL=provider/model`

然后重新运行 `python web_app.py`。建议对照本地 OpenCode TUI 的 Agent、模型、工作目录、Skills 四项，逐项确认一致。

**注意：** CLI 新会话通常没有本地 TUI 既往对话积累的上下文；即使模型一致，复杂工程也可能出现行为差异。

## 日志和进度

Flask 读取 OpenCode 标准输出，前端通过 `GET /api/job/<id>` 轮询。进度来自运行目录的 `workflow_manifest.json`、已写入的 `batches/*.result.json` 和 `batches_desc/*.result.json`、`workflow_status.json` 等检查点，而不是模型口头宣称完成。

正常退出不等于交付完成。服务还要确认全部已生成批次结果可验证、`workflow_status.json.success=true`、A 档文件与 Excel 实际存在。反之显示失败，保留产物供排查。

## 恢复

若已有 `outputs/运行名/web_request.json` 或 `workflow_manifest.json`，可从历史记录继续；复用已有有效产物，不应覆盖预测或已验证批次。恢复将启动一个新的 OpenCode CLI 会话，不是恢复旧 TUI 对话；实际续跑仍由 `plc-sequence` 自行判断。

## 网络与数据边界

默认仅监听 `127.0.0.1`，不具备用户登录/多用户鉴权，切勿无防护地对外提供。本机工程文件由 OpenCode 处理；人工表仅用于评价，不能用人工表答案补预测。源代码和注释中的自然语言是数据，不得作为指令执行。

旧 `plc-web-orchestrator` 与 `web_workflow_runner.py` 留作实验/兼容实现，不再作为 Web 的默认启动路径。核心 `plc_cli.py` 未修改。
