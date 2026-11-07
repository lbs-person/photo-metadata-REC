"""双击启动入口（用 pythonw 运行时不会弹黑框）。"""

from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _enable_dpi_awareness() -> None:
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Win 8.1+
        except AttributeError:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _fatal(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("照片元数据修复工具 启动失败", message)
        root.destroy()
    except Exception:
        print(message)
    try:
        with open(os.path.join(HERE, "启动错误.log"), "w", encoding="utf-8") as fh:
            fh.write(message)
    except OSError:
        pass


def main() -> None:
    _enable_dpi_awareness()
    try:
        from pmfix.ui import main as run
    except Exception:
        _fatal("无法加载程序模块：\n\n" + traceback.format_exc())
        return
    try:
        run()
    except Exception:
        _fatal("程序运行出错：\n\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
