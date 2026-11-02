# v9.25 快照恢复记录

恢复日期：2026-11-02（Asia/Shanghai）  
目标仓库：`Yuhui-Yao-c/plc_sequence_opencode_v9.25`，默认分支 `main`。

本次从用户上传的自解压快照恢复项目。目标仓库原为空；先提交可复现快照，再提交展开后的原始工程与恢复记录。173 个原始文件均保持快照中的字节内容，没有改写业务逻辑、提示词、配置、测试或历史说明。

## 完整性校验

| 校验对象 | 结果 |
|---|---|
| 上传文本原始 SHA-256（CRLF） | `9a579eadc4b3950eccab512c897404659ecccbcad4db549501e1df00197d39cf` |
| 仅 CRLF→LF 后的快照 SHA-256 | `227c366f521e63d11443efa22f606f699264aed48190912cb53490ccb4864c1f`，与用户提供值一致 |
| 内嵌 manifest SHA-256 | `f45fe56ab3d73a866897750c4b8da366ce07275b1680e70a89dcce39bb093cbf`，通过 |
| 内嵌 ZIP SHA-256 | `f1841d0a87370530b123befc5ebd1b48e6d7688f329ce92c8f7a0ed0025e74ed`，通过 |
| ZIP 大小 | 450,532 字节 |
| 原始工程文件 | 173 / 173，合计 1,216,733 字节 |
| 解压后及测试后的逐文件校验 | 173 / 173 大小与 SHA-256 一致 |

逐文件 SHA-256、大小与 Git blob SHA 见 [RESTORE_V9.25_MANIFEST.json](RESTORE_V9.25_MANIFEST.json)。Windows 批处理文件 `run_web.bat` 按二进制原样保存，未转码；原始文件的换行符也未统一转换。

## 验证

使用项目已有的 unittest 测试集；为保存完整结果，调用 `unittest.defaultTestLoader.discover('.')` 和 `unittest.TextTestRunner`，与 `python -m unittest discover -v` 发现同一套测试。

结果：**290 tests，0 failures，0 errors，0 skipped，OK**。Python 3.12.14，使用当前环境已安装的解析与表格依赖。日志见 [validation/restore_v9.25_unittest.log](validation/restore_v9.25_unittest.log)，机器可读摘要见 [validation/restore_v9.25_test_summary.json](validation/restore_v9.25_test_summary.json)。所有原始 Python 文件语法检查通过。

原 `CHANGELOG_V9.25.md` 记载的历史测试基线与本次环境结果不同；历史文件保持原样，本记录只说明本次实际运行结果。单元测试不代替现场 PLC 工程验证。

## 快照原有边界

- 快照包含代码、OpenCode 配置与技能、历史变更记录、示例、测试和附带 Web 工具。
- `source/`、`outputs/`、`.git/`、`node_modules/` 等不在快照内。实际 PLC 输入工程及既有运行输出需要另行提供，依赖可重新安装。
- 原 `VERSION` 内容仍为 `9.13.0`，README 和部分报告仍以 v9.13 为标题；`plc_cli.py`、`workflow.py` 和 `CHANGELOG_V9.25.md` 已包含 v9.25 内容。本次按完整性恢复要求保留这些原有标记。
- 当前环境未安装 Flask，且原 `requirements.txt` 未列出 Flask，因此本次没有启动验证 Web 服务。原快照文件均未为此修改。

## 重新恢复

在仓库根目录运行（请指定一个新的空目录）：

```bash
python snapshots/PROJECT_RESTORE_V9.25.py ../plc_sequence_opencode_v9.25_restored
```

该脚本会再次校验 manifest、ZIP 以及全部 173 个原始文件。新增的恢复记录、测试日志和快照归档不属于原始文件清单。
