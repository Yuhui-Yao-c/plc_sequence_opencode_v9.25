# 前端使用链路诊断与修复（2026-11-04）

本次修复以 `18bf49d295437fa295c12fd214d38c7e73bc99e0` 为基线。结论是：旧页面能够打开，但任务启动、进程管理、状态反馈和交付展示之间存在断点；“页面可访问”和“完整流程可执行”此前没有得到同一条链路的验证。

上次同步保留了旧 Flask 入口，而附件已更新为 Node 前端，这会把用户引向不匹配的后端。本次将旧入口改为兼容包装，统一调用同一个 Node 服务。

## 已从代码确认的问题

| 环节 | 原因与实际表现 | 本次处理 |
| --- | --- | --- |
| 启动入口 | `run_web.bat` 启动旧 Flask，`启动前端.bat` 启动 Node；两者占用同一端口，接口和前端版本不一致。Flask 又不在依赖清单中。 | 两个批处理和 `python web_app.py` 统一启动 Node；浏览器只在监听成功后打开。端口冲突显示具体错误，不自动打开其他版本。 |
| 任务提交 | 旧 `handlePredict` 使用 `exec(start ... cmd /c 临时bat)`，写出批处理就返回 HTTP 200，没有等待进程启动或检查退出码。 | 接口创建受管理任务并返回 HTTP 202 和 job_id；后端直接托管真实子进程。 |
| 任务初期 | `.runlink` 创建时，输出目录和 manifest 可能尚不存在；前端只轮询 run/status，捕获 404 后忽略。 | 从任务创建起轮询 job 接口，源码解析尚未产出 manifest 时也能查看阶段、日志及错误。 |
| 运行与失败 | 没有 PID、标准输出、标准错误、运行锁和终止机制；模型启动失败时页面继续等待结果文件。 | 保存进程状态、日志、退出码对应错误，单任务锁防止并发写入；支持停止整个进程树、服务重启后的中断提示与续跑。 |
| Windows 参数 | 用户路径和长提示词被拼入 CMD 批处理，替换双引号不能解决全部 CMD 元字符、变量展开和命令长度问题。 | Python 使用独立 argv；OpenCode 提示词通过 UTF-8 stdin 发送并显式 EOF。npm 的 `.cmd` 启动器只接收固定工具参数。 |
| 中文任务名 | 前端对任务名编码，旧路由正则捕获后直接查目录，百分号编码未还原；同名 XML 还可能复用旧目录和旧 success 文件。 | URL 一次解码，任务标识改为规范路径的摘要 ID；同名目录自动生成新运行目录，不覆盖既有预测。 |
| 完成判断 | 只看 workflow_status.success 或 result 文件个数，忽略 B 档缺失、错名结果、损坏 JSON、Excel 失败等情况。 | 按 manifest/input 对应结果逐项核对，联合进程退出、两类批次、finish 状态与交付文件判断。区分完成、部分完成、未完成、失败、停止和中断。 |
| 续跑 | 旧入口重新发送一段“继续”文字，页面无法知道实际执行结果；已有但被 finish 拒绝的批次也没有修复路径。 | 继续沿用原运行目录，不重新 predict、不使用 --force；向模型提交缺失/无效或校验拒绝的批次，再由后端运行 finish。 |
| 下载列表 | 旧 listGlob 只处理 `*.ext` 或完整文件名，`*_步序采集表.xlsx`、`*_覆盖评价.xlsx` 匹配不到；Trace 链接还可能指向不存在的文件。 | 使用实际文件清单，枚举 Excel、评价、Trace、CSV 和复核记录；中文下载名使用 UTF-8 Content-Disposition。 |
| 预览数据 | CSV 按换行切分会损坏多行单元格；描述只按相同起止标签映射，会把不同工艺行的描述混为一条。 | 解析带引号的逗号/换行/转义引号；优先按行号与两端标签对应，只有唯一标签对才回退匹配。 |
| 校准开关 | 关闭校准后，隐藏人工表输入中的旧路径仍会随请求发送。 | 前后端都要求明确启用 calibration 才接收人工表；预测仍不读取 GT，人工表仅进入 finish 后的独立评价。 |
| 环境检查 | 没有版本/依赖检查，文件选择器不可用时还被当成取消；覆盖评价导入 xlrd，依赖清单却未列出。 | 新增健康检查，补充 xlrd，区分文件选择取消和错误；没有 GUI 时提示直接粘贴路径。 |

以上是仓库代码能够确认的缺陷。本机模型凭据、所选 provider、OpenCode 安装状态和外部网络是否正常，不能仅凭这份代码判断；这些故障现在会在页面的环境检查或任务日志中明确出现。

## 修复后的执行方式

| 阶段 | 执行方 | 产物与判定 |
| --- | --- | --- |
| 环境检查 | Node 后端 | 检查 Node 18+、Python 3.10+、lxml/openpyxl/pandas/xlrd、OpenCode 可执行程序和 PLC Agent 文件；不冒充模型连通性测试。 |
| 源码解析 | 后端调用 `python -u plc_cli.py predict` | 用户路径、变量模式和输出目录逐项传参，生成预测证据与工艺/B档 input 批次。 |
| 模型分析 | 后端托管 `opencode run --agent plc-sequence` | 提示词从 stdin 传入，模型逐批解释证据并写 result.json；不使用脚本替代模型语义，不读取人工表或 validation 答案。 |
| 交付校验 | 后端调用 `python -u plc_cli.py finish` | 两类批次齐全且结构有效后才执行；原 Python 校验继续负责证据、事件和工艺契约。 |
| 展示与下载 | 页面读取任务和实际文件 | CSV、Excel、评价、Trace 和复核记录均按实际存在的文件展示。Excel/描述/评价/Trace 有问题时显示部分完成并保留可用产物。 |

后端不再依赖额外启动 `opencode serve`，也不再使用旧的 `/api/oc` 反向代理。OpenCode 自己仍使用用户已经配置的模型与凭据；本次没有写入任何模型密钥。

临时权限仅传入本次 OpenCode 子进程：允许选定运行目录的读写和选定源码的读取，人工表路径明确拒绝模型读取，禁止交互式 question 导致无人值守等待。它保留预测/评价隔离规则，但不是操作系统沙箱。不会修改用户全局 OpenCode 配置。

任务日志、注册表和中断记录保存在 `outputs/.web-state/`，沿用 outputs 的 Git 忽略与原打包排除范围，不会混入源码。服务正常停止时终止当前任务；异常退出后若原任务的子进程仍存活，会提示先显式停止原任务，再允许续跑。

## 使用

1. 在当前 Python 环境执行 `python -m pip install -r requirements.txt`。
2. 确认 `node --version` 为 18+，`python --version` 为 3.10+，且 `opencode --version` 能运行。模型配置与认证按原 OpenCode 配置准备。
3. Windows 双击 `run_web.bat` 或 `启动前端.bat`。也可运行 `node server.js` 后打开 `http://127.0.0.1:5173`；旧 `python web_app.py` 入口同样转入 Node。
4. 查看环境检查，填写 XML 与变量模式。需要评价时开启校准模式再指定人工表。点击开始执行，直接查看本页阶段、日志和两类批次进度。
5. 任务未完成时使用继续处理；停止任务不会删除源码或已完成批次。已有交付和复核文件可直接下载。

| 可选变量 | 含义 |
| --- | --- |
| PLC_WEB_PORT | 工作台端口，默认 5173。 |
| PLC_PYTHON | Python 可执行文件路径，不能填写 `py -3` 等带参数命令。默认在 PATH 查找 python/python3/py。 |
| PLC_OPENCODE | OpenCode 可执行文件路径；Windows 支持 npm 安装的 opencode.cmd。 |
| PLC_JOB_TIMEOUT_MS | 每个执行阶段的最长时间，默认 6 小时；页面始终提供日志和停止操作。 |

例如 PowerShell：

```powershell
$env:PLC_PYTHON = 'C:\Python312\python.exe'
$env:PLC_OPENCODE = 'C:\Users\你的用户名\AppData\Roaming\npm\opencode.cmd'
node server.js
```

OpenCode 的版本、模型或认证不兼容时，日志保留原始错误；请先用同一终端检查 OpenCode 配置，再点击继续处理。外部目录权限拒绝、401、连接超时等不会被静默当成“仍在生成结果”。

## 验证与边界

验证覆盖原 Python 回归、Node HTTP/子进程链路、真实浏览器操作，以及仓库 XML 示例到真实 Python 交付表的组合验证。结果与环境见 `validation/web_repair_20261104_summary.json`，各项日志保存在同目录。修改文件及 SHA256 见 `WEB_REPAIR_20261104_FILES.json`。

| 本次检查 | 结果 |
| --- | --- |
| 原有 Python 单元测试 | 290 通过，0 失败/错误/跳过 |
| Node 与 HTTP/进程回归 | 23 通过，0 失败/跳过 |
| Chromium 浏览器验收项目 | 14 项通过，0 页面脚本错误 |
| 真实示例 XML 与 Python 组合链路 | 原生欧姆龙示例解析得到 3 个候选、1 行最终标签对；Excel 经 openpyxl 读取验证；源码字节保持不变 |
| JavaScript 语法 | server.js、web_runtime.js、web/app.js 通过 |

模型替身只用于隔离测试，日志与代码均明确标注 synthetic fixture。真实 XML 组合测试将工艺事件明确标为未经过模型复核，并由真实 Python 生成预测主表与 Excel，再用 openpyxl 验证工作簿可读取。它验证接口衔接，不证明生产工艺语义质量。

没有用户本机 OpenCode/模型凭据和生产 XML，不能声称已经验证真实模型调用或现场工艺正确性。Windows 启动脚本和 npm `.cmd` 的参数构造已检查，实际 Windows 进程启动与原生 tkinter 弹窗仍需在 Windows 环境验收。

复跑：

```text
python -B -W ignore::ResourceWarning -m unittest discover -v
npm test
npm run test:pipeline
```

可选浏览器验收需要测试环境安装 Playwright 及 Chromium，然后运行 `npm run test:browser`。`PLC_TEST_CHROMIUM` 可指定已有 Chromium 路径，`PLC_TEST_ARTIFACTS` 可指定测试摘要与截图目录。生产运行不需要这些浏览器测试依赖，也不需要 npm install。

接口参考：[OpenCode CLI](https://opencode.ai/docs/cli/)、[权限配置](https://opencode.ai/docs/permissions/)。源码的解析与评价算法未在本次 Web 修复中改写。
