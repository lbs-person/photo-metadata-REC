"""界面层的无人工干预冒烟测试：把不弹窗、不等待的路径都走一遍。"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from datetime import datetime  # noqa: E402

from pmfix import scanner, ui  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    demo = os.path.join(ROOT, "demo_icloud")
    if not os.path.isdir(demo):
        print("缺少 demo_icloud 演示目录")
        return 2

    calls = []
    ui.messagebox.askyesno = lambda *a, **k: True
    ui.messagebox.showinfo = lambda *a, **k: calls.append(("info", a))
    ui.messagebox.showwarning = lambda *a, **k: calls.append(("warn", a))
    ui.messagebox.showerror = lambda *a, **k: calls.append(("error", a))
    csv_path = os.path.join(demo, "report_test.csv")
    ui.filedialog.asksaveasfilename = lambda **k: csv_path

    app = ui.App()
    app.withdraw()

    def pump(cond, timeout=40.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            app.update()
            if cond():
                return True
            time.sleep(0.05)
        return False

    # ---- 扫描
    app.path_var.set(demo)
    app.start_scan()
    ok = pump(lambda: app.rows and not app.busy)
    check("扫描完成", ok and len(app.rows) == 6, f"{len(app.rows)} 行")

    # ---- 表格渲染
    app.refresh_tree()
    kids = app.tree.get_children()
    check("表格有行", len(kids) > 0, str(len(kids)))
    first = app.tree.item(kids[0], "values")
    check("行内容完整", len(first) == 8, str(first))

    # ---- 选中与详情
    app.tree.selection_set(kids[0])
    app._refresh_detail()
    detail = app.detail.get("1.0", "end").strip()
    check("详情面板有内容", len(detail) > 20, detail[:60])

    # ---- 排序
    app.sort_by("name")
    names = [app.tree.item(k, "values")[1] for k in app.tree.get_children()]
    check("按文件名排序生效", names == sorted(names, key=str.lower), str(names))
    app.sort_by("conf")
    confs = [app.tree.item(k, "values")[6] for k in app.tree.get_children()]
    check("按置信度排序不报错", len(confs) == len(names))

    # ---- 勾选切换
    before = sum(1 for r in app.rows if r.selected)
    app.set_all(False)
    check("全不选", sum(1 for r in app.rows if r.selected) == 0)
    app.set_all(True)
    check("全选", sum(1 for r in app.rows if r.selected) == len(app.rows))
    app.select_actionable()
    after = sum(1 for r in app.rows if r.selected)
    check("仅选可修复", 0 < after < len(app.rows), f"{before}->{after}")

    # ---- 手动指定（绕过对话框，直接验证数据层效果）
    target = app.rows[0]
    app.tree.selection_set(app.tree.get_children()[0])
    picked = app._visible[0]
    picked.manual = {"dt_local": datetime(2010, 10, 10, 10, 10, 10), "offset": None,
                     "source": "manual", "note": "手动指定", "weight": 100}
    check("手动指定生效", picked.target_dt == datetime(2010, 10, 10, 10, 10, 10)
          and picked.confidence == 100 and picked.source_label == "手动指定")
    picked.manual = None

    # ---- 导出 CSV
    app.export_csv()
    check("导出 CSV", os.path.isfile(csv_path) and os.path.getsize(csv_path) > 100)
    try:
        os.remove(csv_path)
    except OSError:
        pass

    # ---- 修复
    app.select_actionable()
    app.start_repair()
    ok = pump(lambda: not app.busy, timeout=60)
    check("修复流程跑完", ok)
    errs = [c for c in calls if c[0] == "error"]
    check("修复过程无弹窗错误", not errs, str(errs)[:300])
    done = [r for r in app.rows if r.status == scanner.ST_OK]
    check("修复后有文件状态变为已有", len(done) >= 2, str(len(done)))

    # ---- 再扫一次，确认落盘
    app.start_scan()
    pump(lambda: app.rows and not app.busy)
    dates = {r.name: r.exif_dt for r in app.rows}
    check("落盘校验：JPG 有拍摄时间",
          dates.get("2019-07-04 12.34.56.jpg") == datetime(2019, 7, 4, 12, 34, 56),
          str(dates.get("2019-07-04 12.34.56.jpg")))

    # ---- 手动覆盖一张「已有拍摄时间」的照片，验证会真的改写 EXIF
    heic = next((r for r in app.rows if r.name == "IMG_3333.HEIC"), None)
    if heic is not None:
        heic.manual = {"dt_local": datetime(2005, 5, 5, 5, 5, 5), "offset": None,
                       "source": "manual", "note": "手动指定", "weight": 100}
        heic.selected = True
        for r in app.rows:
            if r is not heic:
                r.selected = False
        app.start_repair()
        pump(lambda: not app.busy, timeout=60)
        app.start_scan()
        pump(lambda: app.rows and not app.busy)
        again = {r.name: r.exif_dt for r in app.rows}
        check("手动指定能覆盖已有的 EXIF",
              again.get("IMG_3333.HEIC") == datetime(2005, 5, 5, 5, 5, 5),
              str(again.get("IMG_3333.HEIC")))

    app.destroy()
    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
