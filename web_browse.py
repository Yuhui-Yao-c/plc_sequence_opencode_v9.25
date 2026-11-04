#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tkinter 文件/目录选择对话框，被 web_app.py 以 subprocess 方式调用。
输出选中路径到 stdout；取消则输出空行。无 tkinter 时输出空行退出。"""
import sys

try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:
    print("")
    sys.exit(0)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "file"
    title = sys.argv[2] if len(sys.argv) > 2 else "选择文件"
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if mode == "dir":
        path = filedialog.askdirectory(title=title, parent=root)
    else:
        path = filedialog.askopenfilename(
            title=title,
            parent=root,
            filetypes=[
                ("PLC源码/Excel/Trace", "*.xml *.smc2 *.txt *.xls *.xlsx *.trace"),
                ("XML源码", "*.xml *.smc2 *.txt"),
                ("Excel表格", "*.xls *.xlsx"),
                ("Trace文件", "*.trace"),
                ("所有文件", "*.*"),
            ],
        )
    print(path if path else "")
    root.destroy()


if __name__ == "__main__":
    main()
