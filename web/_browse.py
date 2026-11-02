"""文件对话框辅助脚本 — 通过 tkinter 打开原生文件/目录选择对话框。
被 web_app.py 的 /api/browse 端点以子进程方式调用，输出所选路径到 stdout。"""
import sys
import tkinter as tk
from tkinter import filedialog


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "file"
    file_filter = sys.argv[2] if len(sys.argv) > 2 else ""
    title = sys.argv[3] if len(sys.argv) > 3 else "选择文件"

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    path = ""
    if mode == "dir":
        path = filedialog.askdirectory(title=title)
    else:
        filetypes = [("所有文件", "*.*")]
        if file_filter == "xml":
            filetypes = [("XML 文件", "*.xml"), ("所有文件", "*.*")]
        elif file_filter == "trace":
            filetypes = [("Trace 文件", "*.trace"), ("所有文件", "*.*")]
        elif file_filter == "excel":
            filetypes = [("Excel 文件", "*.xls;*.xlsx"), ("所有文件", "*.*")]
        path = filedialog.askopenfilename(title=title, filetypes=filetypes)

    print(path if path else "")
    root.destroy()


if __name__ == "__main__":
    main()
