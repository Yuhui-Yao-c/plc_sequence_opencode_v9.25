#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Node 后端调用的文件选择器；stdout 仅返回路径，取消返回空行。"""
import sys

try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:
    print("Python 未安装 tkinter；请安装它，或直接在网页粘贴路径。", file=sys.stderr)
    sys.exit(2)


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
    try:
        main()
    except Exception as exc:
        print(f"无法打开本机文件选择器：{exc}。可直接在网页粘贴路径。", file=sys.stderr)
        sys.exit(2)
