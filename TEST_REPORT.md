# v9.13 测试报告

执行：`python -W ignore::ResourceWarning -m unittest discover -v`
结果：290 tests，OK。日志在test-results-v9.13.txt。
其中历史255项和新增35项均通过。旧测试文件的ResourceWarning为未关闭测试读取句柄，此处仅关闭该警告显示，测试断言未修改。
完整源码及只读评价范围详见RELEASE_v9_13.md，不将单元测试通过等同于工业现场验证。
