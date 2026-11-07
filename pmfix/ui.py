"""Tkinter 图形界面。"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import traceback
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import scanner, repair, report, __version__

BG = "#f7f8fa"
PANEL = "#ffffff"
FG_MUTED = "#6b7280"

TAG_STYLE = {
    scanner.ST_FIX: ("#e8f6ec", "#1a7f37"),
    scanner.ST_LOW: ("#fff6e0", "#9a6700"),
    scanner.ST_PARTIAL: ("#eef2ff", "#3730a3"),
    scanner.ST_NONE: ("#fdecec", "#b42318"),
    scanner.ST_OK: ("#f3f4f6", "#4b5563"),
    scanner.ST_VIDEO: ("#e7f3ff", "#0b5cad"),
}

COLUMNS = [
    ("sel", "勾选", 46, "center"),
    ("name", "文件名", 260, "w"),
    ("status", "状态", 110, "w"),
    ("exif", "当前 EXIF 拍摄时间", 150, "w"),
    ("target", "建议时间", 150, "w"),
    ("source", "来源", 110, "w"),
    ("conf", "置信度", 66, "center"),
    ("mtime", "文件时间", 150, "w"),
]

TZ_CHOICES = ["不写入", "+08:00", "+09:00", "+00:00", "-05:00", "-08:00", "+05:30"]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"照片元数据修复工具  v{__version__}")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = max(900, min(1180, sw - 80))
        h = max(520, min(720, sh - 140))
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(900, 500)
        self.configure(bg=BG)

        self.rows: list[scanner.Row] = []
        self.root_dir = ""
        self.queue: queue.Queue = queue.Queue()
        self.busy = False
        self.sort_key = None
        self.sort_reverse = False
        self.only_actionable = tk.BooleanVar(value=True)
        self.recursive = tk.BooleanVar(value=True)
        self.include_video = tk.BooleanVar(value=True)
        self.backup = tk.BooleanVar(value=True)
        self.tz = tk.StringVar(value="不写入")
        self.filter_text = tk.StringVar()
        self.summary_text = tk.StringVar(value="选择照片文件夹后点击「扫描」")
        self._base_summary = ""
        self._visible: list[scanner.Row] = []

        self._build_style()
        self._build_ui()
        self.after(100, self._poll)

    # ------------------------------------------------------------ 外观
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
        family = "Microsoft YaHei UI"
        self.option_add("*Font", (family, 9))
        style.configure(".", font=(family, 9))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL, relief="flat")
        style.configure("TLabel", background=BG, foreground="#111827")
        style.configure("Muted.TLabel", background=BG, foreground=FG_MUTED)
        style.configure("Title.TLabel", font=(family, 12, "bold"), background=BG)
        style.configure("TButton", padding=(10, 4))
        style.configure("Accent.TButton", font=(family, 9, "bold"), padding=(14, 6))
        style.configure("Treeview", rowheight=24, background=PANEL,
                        fieldbackground=PANEL, borderwidth=0)
        style.configure("Treeview.Heading", font=(family, 9, "bold"), padding=(4, 6))
        style.map("Treeview", background=[("selected", "#cfe3ff")],
                  foreground=[("selected", "#0b1b33")])
        style.configure("TCheckbutton", background=BG)
        style.configure("TRadiobutton", background=BG)

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        header = ttk.Frame(self)
        header.pack(fill="x", **pad)
        ttk.Label(header, text="照片元数据修复工具",
                  style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  iCloud / 网盘下载后拍摄时间丢失？按文件名、EXIF、视频、目录名综合推断并写回",
                  style="Muted.TLabel").pack(side="left")

        # 路径行
        row1 = ttk.Frame(self)
        row1.pack(fill="x", **pad)
        ttk.Label(row1, text="照片文件夹").pack(side="left")
        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(row1, textvariable=self.path_var)
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(row1, text="浏览…", command=self.browse).pack(side="left")
        self.btn_scan = ttk.Button(row1, text="扫描", style="Accent.TButton",
                                   command=self.start_scan)
        self.btn_scan.pack(side="left", padx=(8, 0))

        # 选项行
        row2 = ttk.Frame(self)
        row2.pack(fill="x", padx=12)
        ttk.Checkbutton(row2, text="包含子文件夹", variable=self.recursive).pack(side="left")
        ttk.Checkbutton(row2, text="包含视频", variable=self.include_video).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(row2, text="写入前备份原文件", variable=self.backup).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(row2, text="只显示需要处理的", variable=self.only_actionable,
                        command=self.refresh_tree).pack(side="left", padx=(12, 0))
        ttk.Label(row2, text="时区偏移", style="Muted.TLabel").pack(side="left", padx=(18, 4))
        ttk.Combobox(row2, textvariable=self.tz, values=TZ_CHOICES, width=9).pack(side="left")
        ttk.Label(row2, text="（写入 EXIF 的 OffsetTime，一般选「不写入」）",
                  style="Muted.TLabel").pack(side="left", padx=(6, 0))

        # 工具条
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12, pady=(8, 4))
        self.btn_all = ttk.Button(bar, text="全选", width=8, command=lambda: self.set_all(True))
        self.btn_all.pack(side="left")
        self.btn_none = ttk.Button(bar, text="全不选", width=8, command=lambda: self.set_all(False))
        self.btn_none.pack(side="left", padx=4)
        self.btn_actionable = ttk.Button(bar, text="仅选可修复", width=10, command=self.select_actionable)
        self.btn_actionable.pack(side="left")
        self.btn_edit = ttk.Button(bar, text="手动指定时间…", command=self.edit_selected)
        self.btn_edit.pack(side="left", padx=(12, 0))

        ttk.Label(bar, text="筛选", style="Muted.TLabel").pack(side="left", padx=(16, 4))
        ttk.Entry(bar, textvariable=self.filter_text, width=16).pack(side="left")
        self.filter_text.trace_add("write", lambda *_: self.refresh_tree())

        self.btn_apply = ttk.Button(bar, text="开始修复", style="Accent.TButton",
                                    command=self.start_repair)
        self.btn_apply.pack(side="right")
        self.btn_csv = ttk.Button(bar, text="导出 CSV", command=self.export_csv)
        self.btn_csv.pack(side="right", padx=6)
        self.btn_restore = ttk.Button(bar, text="还原备份", command=self.restore_backup)
        self.btn_restore.pack(side="right")

        # 表格
        table_wrap = ttk.Frame(self)
        table_wrap.pack(fill="both", expand=True, padx=12)
        columns = [c[0] for c in COLUMNS]
        self.tree = ttk.Treeview(table_wrap, columns=columns, show="headings",
                                 selectmode="extended")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key in ("name",)))
        for status, (bg, fg) in TAG_STYLE.items():
            self.tree.tag_configure(status, background=bg, foreground=fg)

        vsb = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.edit_selected())
        self.tree.bind("<space>", lambda e: self.toggle_selected())
        self.tree.bind("<Button-1>", self._on_click, add="+")

        # 详情
        detail = ttk.Frame(self, style="Card.TFrame")
        detail.pack(fill="x", padx=12, pady=(6, 0))
        self.detail = tk.Text(detail, height=6, wrap="word", relief="flat",
                              bg=PANEL, fg="#1f2937", padx=10, pady=8,
                              font=("Microsoft YaHei UI", 9))
        self.detail.pack(fill="x")
        self.detail.configure(state="disabled")

        # 状态栏
        footer = ttk.Frame(self)
        footer.pack(fill="x", padx=12, pady=8)
        self.progress = ttk.Progressbar(footer, mode="determinate", length=180)
        self.progress.pack(side="right")
        ttk.Label(footer, textvariable=self.summary_text, style="Muted.TLabel").pack(side="left")

    # ------------------------------------------------------------ 工具
    def browse(self):
        d = filedialog.askdirectory(title="选择照片文件夹")
        if d:
            self.path_var.set(os.path.normpath(d))
            self.start_scan()

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.btn_scan, self.btn_apply, self.btn_csv, self.btn_restore,
                    self.btn_all, self.btn_none, self.btn_actionable, self.btn_edit):
            btn.configure(state=state)

    def _offset(self) -> str | None:
        raw = self.tz.get().strip()
        if re.fullmatch(r"[+-]\d{2}:\d{2}", raw):
            return raw
        return None

    def _visible_rows(self):
        rows = self.rows
        if self.only_actionable.get():
            rows = [r for r in rows if r.status in (scanner.ST_FIX, scanner.ST_LOW,
                                                    scanner.ST_PARTIAL, scanner.ST_VIDEO)
                    or (r.status == scanner.ST_OK and r.selected)]
        text = self.filter_text.get().strip().lower()
        if text:
            rows = [r for r in rows if text in r.name.lower() or text in r.rel.lower()]
        return rows

    # ------------------------------------------------------------ 扫描
    def start_scan(self):
        if self.busy:
            return
        path = self.path_var.get().strip().strip('"')
        if not path or not os.path.isdir(path):
            messagebox.showwarning("提示", "请先选择一个存在的照片文件夹。")
            return
        self.root_dir = path
        self._set_busy(True)
        self.progress.configure(value=0, maximum=100)
        self.summary_text.set("正在扫描…")
        # 注意：Tk 变量只能在主线程读取，这里先取值再交给工作线程
        recursive = self.recursive.get()
        include_video = self.include_video.get()
        threading.Thread(target=self._scan_worker, args=(path, recursive, include_video),
                         daemon=True).start()

    def _scan_worker(self, path: str, recursive: bool, include_video: bool):
        try:
            def cb(i, total, name):
                self.queue.put(("progress", i, total, name))
            rows = scanner.scan(path, recursive=recursive,
                                include_video=include_video, progress=cb)
            self.queue.put(("scanned", rows))
        except Exception:
            self.queue.put(("error", traceback.format_exc()))

    # ------------------------------------------------------------ 修复
    def start_repair(self):
        if self.busy:
            return
        todo = [r for r in self.rows if r.selected]
        if not todo:
            messagebox.showinfo("提示", "没有勾选任何文件。\n\n"
                                        "提示：点击「仅选可修复」可以自动勾选所有能推断出时间的照片。")
            return
        no_time = [r for r in todo if r.target_dt is None]
        if no_time:
            messagebox.showwarning("提示",
                                   f"有 {len(no_time)} 个勾选的文件没有可用时间，将保持不变。")
        mode = "备份后" if self.backup.get() else "直接"
        if not messagebox.askyesno("确认修复",
                                   f"即将{mode}修改 {len(todo)} 个文件。\n\n"
                                   f"· 备份位置：文件夹内的 .pmfix_backup\n"
                                   f"· 写回内容：EXIF 拍摄时间 + 文件修改时间\n\n"
                                   f"确定继续吗？"):
            return
        self._set_busy(True)
        self.progress.configure(value=0, maximum=100)
        self.summary_text.set("正在修复…")
        backup = self.backup.get()
        offset = self._offset()
        threading.Thread(target=self._repair_worker, args=(todo, backup, offset),
                         daemon=True).start()

    def _repair_worker(self, todo, backup: bool, offset):
        try:
            def cb(i, total, name):
                self.queue.put(("progress", i, total, name))
            results = repair.apply(self.root_dir, todo,
                                   write_exif=True, write_fs=True,
                                   backup=backup, offset=offset, progress=cb)
            self.queue.put(("repaired", results))
        except Exception:
            self.queue.put(("error", traceback.format_exc()))

    # ------------------------------------------------------------ 队列
    def _poll(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _k, i, total, name = msg
                    self.progress.configure(maximum=max(total, 1), value=i)
                    if total:
                        self.summary_text.set(f"{name}  ({i}/{total})")
                elif kind == "scanned":
                    self._on_scanned(msg[1])
                elif kind == "repaired":
                    self._on_repaired(msg[1])
                elif kind == "error":
                    self._set_busy(False)
                    self.summary_text.set("出错了")
                    messagebox.showerror("错误", msg[1][-2000:])
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _on_scanned(self, rows):
        self.rows = rows
        self._set_busy(False)
        self.progress.configure(maximum=100, value=0)
        stats = scanner.summarize(rows)
        warn = scanner.suspicious_uniform(rows)
        text = (f"共 {stats['total']} 个文件：可修复 {stats.get(scanner.ST_FIX, 0)}，"
                f"低置信 {stats.get(scanner.ST_LOW, 0)}，"
                f"不完整 {stats.get(scanner.ST_PARTIAL, 0)}，"
                f"已有拍摄时间 {stats.get(scanner.ST_OK, 0)}，"
                f"无法推断 {stats.get(scanner.ST_NONE, 0)}")
        if warn:
            text += "　⚠ " + warn
        self._base_summary = text
        self.refresh_tree(select_actionable=True)
        if not rows:
            messagebox.showinfo("提示", "这个文件夹里没有找到支持的照片或视频。")

    def _on_repaired(self, results):
        self._set_busy(False)
        self.progress.configure(maximum=100, value=0)
        ok = sum(1 for r in results if r.ok)
        self._base_summary = f"修复完成：{ok}/{len(results)} 成功"
        for row in self.rows:
            if row.status in (scanner.ST_FIX, scanner.ST_LOW, scanner.ST_PARTIAL):
                row.selected = False
        self.refresh_tree()
        if ok < len(results):
            bad = [r for r in results if not r.ok][:10]
            detail = "\n".join(f"{os.path.basename(r.path)}：{r.message}" for r in bad)
            messagebox.showwarning("部分失败", f"{len(results) - ok} 个文件未能完成：\n\n{detail}")
        else:
            messagebox.showinfo("完成", f"{ok} 个文件已处理。\n\n原文件已备份到 .pmfix_backup 文件夹。")

    # ------------------------------------------------------------ 表格
    def refresh_tree(self, select_actionable: bool = False):
        if select_actionable:
            for r in self.rows:
                if r.status in (scanner.ST_FIX, scanner.ST_LOW, scanner.ST_PARTIAL):
                    r.selected = True
        rows = list(self._visible_rows())
        if self.sort_key:
            rows.sort(key=self._sort_value(self.sort_key), reverse=self.sort_reverse)

        self.tree.delete(*self.tree.get_children())
        for idx, r in enumerate(rows):
            self.tree.insert("", "end", iid=str(idx), values=self._row_values(r),
                             tags=(r.status,))
        self._visible = rows
        self._refresh_detail()
        hidden = len(self.rows) - len(rows)
        if self._base_summary:
            text = self._base_summary
            if hidden > 0:
                text += f"　（已隐藏 {hidden} 个不需要处理的文件）"
            self.summary_text.set(text)

    def _row_values(self, r: scanner.Row):
        exif = r.exif_dt
        try:
            mt = datetime.fromtimestamp(os.path.getmtime(r.path))
        except OSError:
            mt = None
        return (
            "☑" if r.selected else "☐",
            r.name,
            scanner.STATUS_TEXT.get(r.status, r.status),
            exif.strftime("%Y-%m-%d %H:%M:%S") if exif else "—",
            r.target_dt.strftime("%Y-%m-%d %H:%M:%S") if r.target_dt else "—",
            r.source_label,
            r.confidence if r.target_dt else "—",
            mt.strftime("%Y-%m-%d %H:%M:%S") if mt else "—",
        )

    def _sort_value(self, key):
        def get(r):
            if key == "name":
                return r.name.lower()
            if key == "status":
                order = {scanner.ST_FIX: 0, scanner.ST_LOW: 1, scanner.ST_PARTIAL: 2,
                         scanner.ST_NONE: 3, scanner.ST_OK: 4, scanner.ST_VIDEO: 5}
                return order.get(r.status, 9)
            if key == "exif":
                return r.exif_dt or datetime.min
            if key == "target":
                return r.target_dt or datetime.min
            if key == "conf":
                return r.confidence
            if key == "mtime":
                try:
                    return datetime.fromtimestamp(os.path.getmtime(r.path))
                except OSError:
                    return datetime.min
            if key == "sel":
                return 0 if r.selected else 1
            return r.source_label
        return get

    def sort_by(self, key):
        if self.sort_key == key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key, self.sort_reverse = key, False
        self.refresh_tree()

    def _on_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        if col != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        row = self._visible[int(iid)]
        row.selected = not row.selected
        self.tree.set(iid, "sel", "☑" if row.selected else "☐")

    def selected_rows(self) -> list[scanner.Row]:
        out = []
        for iid in self.tree.selection():
            try:
                out.append(self._visible[int(iid)])
            except (IndexError, ValueError):
                pass
        return out

    def on_select(self, _event=None):
        self._refresh_detail()

    def _refresh_detail(self):
        rows = self.selected_rows()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if not rows:
            self.detail.insert("end", "选中一行查看该文件的全部时间线索；第一列可直接点击切换勾选，双击可手动指定时间。")
            self.detail.configure(state="disabled")
            return
        r = rows[0]
        lines = [f"{r.rel}    （{r.kind}，{r.size / 1024:.0f} KB）"]
        if len(rows) > 1:
            lines[0] += f"    另选中 {len(rows) - 1} 个文件"
        if r.exif_dt:
            lines.append(f"当前 EXIF 拍摄时间：{r.exif_dt:%Y-%m-%d %H:%M:%S}")
        else:
            lines.append("当前 EXIF 拍摄时间：缺失")
        lines.append(f"状态：{scanner.STATUS_TEXT.get(r.status, r.status)}"
                     + (f"　（{r.note}）" if r.note else ""))
        if r.candidates:
            lines.append("推断依据（按可信度排序）：")
            for c in r.candidates[:6]:
                off = f" {c['offset']}" if c.get("offset") else ""
                label = scanner._pretty_source(c)
                lines.append(f"　· {c['dt_local']:%Y-%m-%d %H:%M:%S}{off}"
                             f"　{label}（{c['weight']}）— {c.get('note', '')}")
        else:
            lines.append("推断依据：没有找到任何线索，只能手动指定或保持原样。")
        for note in (r.probe.notes or []):
            lines.append(f"注：{note}")
        self.detail.insert("end", "\n".join(lines))
        self.detail.configure(state="disabled")

    def set_all(self, value: bool):
        for r in self.rows:
            r.selected = value
        self.refresh_tree()

    def select_actionable(self):
        for r in self.rows:
            r.selected = r.status in (scanner.ST_FIX, scanner.ST_LOW, scanner.ST_PARTIAL)
        self.refresh_tree()

    def toggle_selected(self):
        for r in self.selected_rows():
            r.selected = not r.selected
        self.refresh_tree()

    # ------------------------------------------------------------ 手动指定
    def edit_selected(self):
        rows = self.selected_rows()
        if not rows:
            messagebox.showinfo("提示", "请先在列表里选中要修改的行（可按住 Ctrl / Shift 多选）。")
            return
        base = rows[0].target_dt or datetime.now()
        dlg = tk.Toplevel(self)
        dlg.title(f"手动指定时间（{len(rows)} 个文件）")
        dlg.transient(self)
        dlg.grab_set()
        dlg.configure(bg=BG)
        dlg.resizable(False, False)

        ttk.Label(dlg, text="时间（YYYY-MM-DD HH:MM:SS）").grid(
            row=0, column=0, sticky="w", padx=14, pady=(14, 4))
        var = tk.StringVar(value=base.strftime("%Y-%m-%d %H:%M:%S"))
        entry = ttk.Entry(dlg, textvariable=var, width=26)
        entry.grid(row=1, column=0, padx=14, sticky="w")
        entry.focus_set()
        entry.select_range(0, "end")

        hint = ttk.Label(dlg, text="将同时写入 EXIF 拍摄时间与文件时间，并标记为「手动指定」。",
                         style="Muted.TLabel")
        hint.grid(row=2, column=0, padx=14, pady=(6, 0), sticky="w")

        def ok(_evt=None):
            text = var.get().strip()
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                        "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                messagebox.showerror("格式错误", "无法识别的时间，请用 YYYY-MM-DD HH:MM:SS 格式。", parent=dlg)
                return
            for r in rows:
                r.manual = {"dt_local": dt, "offset": self._offset(),
                            "source": "manual", "note": "手动指定", "weight": 100}
                if r.status in (scanner.ST_NONE, scanner.ST_LOW, scanner.ST_VIDEO):
                    r.status = scanner.ST_FIX
                r.selected = True
            dlg.destroy()
            self.refresh_tree()

        box = ttk.Frame(dlg)
        box.grid(row=3, column=0, padx=14, pady=14, sticky="e")
        ttk.Button(box, text="取消", command=dlg.destroy).pack(side="right")
        ttk.Button(box, text="确定", style="Accent.TButton", command=ok).pack(side="right", padx=6)
        dlg.bind("<Return>", ok)
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ------------------------------------------------------------ 导出 / 还原
    def export_csv(self):
        if not self.rows:
            messagebox.showinfo("提示", "还没有扫描结果。")
            return
        path = filedialog.asksaveasfilename(
            title="保存报告", defaultextension=".csv",
            initialfile=f"照片元数据报告_{datetime.now():%Y%m%d_%H%M}.csv",
            filetypes=[("CSV 文件", "*.csv"), ("JSON 文件", "*.json")])
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                report.write_json(path, self.rows)
            else:
                report.write_csv(path, self.rows)
            messagebox.showinfo("完成", f"报告已保存：\n{path}")
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))

    def restore_backup(self):
        if not self.root_dir:
            messagebox.showinfo("提示", "请先扫描一个文件夹。")
            return
        base = os.path.join(self.root_dir, repair.BACKUP_DIR)
        if not os.path.isdir(base):
            messagebox.showinfo("提示", "这个文件夹里没有找到 .pmfix_backup 备份。")
            return
        if not messagebox.askyesno("确认还原",
                                   "将用 .pmfix_backup 里的原始文件覆盖当前文件。\n\n"
                                   "注意：修复之后你自己做的修改也会被覆盖。\n\n确定继续吗？"):
            return
        res = repair.restore(self.root_dir)
        ok = sum(1 for r in res if r.ok)
        messagebox.showinfo("完成", f"已还原 {ok}/{len(res)} 个文件。")
        self.start_scan()


def main():
    initial = None
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        initial = os.path.abspath(sys.argv[1])
    app = App()
    if initial:
        app.path_var.set(initial)
        app.after(400, app.start_scan)
    app.mainloop()


if __name__ == "__main__":
    main()
