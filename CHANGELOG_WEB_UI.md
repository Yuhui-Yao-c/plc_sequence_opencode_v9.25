# Web 控制台完整工作流接入

## 本次变更

- 将原有“predict / finish 页面 + OpenCode 人工切换”的半自动界面升级为完整工作流控制台。
- 增加 `web_opencode.py`：发现 OpenCode CLI、读取 workflow_manifest、枚举工艺与 B 档批次、校验 result.json、生成单批 `opencode run` 命令。
- 新增 `plc-process-batch` 与 `plc-action-description` 两个单批 Agent，分别处理工艺语义与 B 档动作描述，避免把两个任务混为一个提示词。
- Flask 新增完整执行接口：predict → 工艺批次 → B 档描述批次 → finish；新增断点恢复、取消任务、环境检测和批次详情接口。
- 每个 OpenCode 调用限定一个批次输入文件和一个结果输出文件；有效结果跳过，缺失/结构无效结果重跑。
- finish 前检查批次结果结构、候选/事件引用及事件覆盖；最终以项目原有 `workflow.validate()` 和 `action_describe.cmd_validate()` 为权威校验。
- 修复旧 Web 接口中 `finish --run` 仅传运行目录 basename 的路径问题；统一使用 outputs 下运行目录的绝对路径。
- 修复非 Windows 环境中无条件引用 `CREATE_NO_WINDOW` 的兼容性问题；增加 `.cmd/.bat` OpenCode shim 支持。
- 修复结果表动作描述按行号合并的错位风险，改按开始/结束标签匹配。
- 增加 Flask 依赖、Web 使用说明与批次结果单元测试。
- 重做 Web 页面为浅色工程控制台，支持环境检查、任务配置、三阶段进度、实时日志、历史记录、批次 JSON 查看、结果搜索与文件下载。

## 数据与安全边界

- 默认只监听 `127.0.0.1`。
- 输出目录限制为项目 `outputs/` 下的一级目录，并拒绝覆盖已有非空运行目录。
- 下载接口拒绝路径穿越。
- 人工表只作为 `--manual` 评价输入，不传给 OpenCode 批次 Agent。
- Web 批次 Agent 不运行 predict / finish；最终 finish 由 Flask 编排器执行一次。
- OpenCode 使用 `opencode run --agent ... --auto --dir ... --file ...` 的非交互式 CLI 调用；明确拒绝的权限仍然遵循 OpenCode 配置。

## 验证

- `python -m py_compile web_app.py web_opencode.py web/_browse.py`
- `python -m unittest discover -s tests -v`
- `node --check web/app.js`
- HTML 静态 ID 引用检查。


## Agent 权限

两个单批 Agent 显式允许 read / glob / grep / list / skill；只允许 `outputs/**` 编辑；显式拒绝 bash、task、外部目录、webfetch 和 websearch。输出路径使用项目相对路径，以匹配受限写权限。

批次 Agent 另行拒绝读取 `.xls`、`.xlsx`、`.xlsm` 文件，确保人工采集表只留在 finish 的评价链路。


## Web Harness 调度入口修正（2026-11-03）

- 将 Flask 先 predict、逐批启动 OpenCode、最后 finish 的硬编排替换为一次 `plc-web-orchestrator` 主 Agent 会话。
- 引入 `web_workflow_runner.py` 的 `predict/status/finish` Python 原子能力。它们由 OpenCode 主 Agent 通过 Bash 决策调用，保留原有 `plc_cli.py` 与 `workflow.validate()`，不重写提取算法。- `plc-process-batch` 与 `plc-action-description` 设为 `mode: all`，允许由主 Agent 的 Task 工具调用，每批隔离上下文。
- 前端仅提交 `web_request.json` 配置；Flask 只负责启动与取消 OpenCode、观察磁盘检查点、下载文件。无独立的阶段调度，旧 `/api/predict` 和 `/api/finish` 统一返回 410。
- 保存请求清单以支持在 predict 尚未完成时恢复运行。OpenCode 正常退出但缺少有效结果或 Excel 时，后台仍明确标记失败。
- 增加 Runner 状态、提前 finish 阻断、恢复跳过已处理 predict、路径安全等测试。