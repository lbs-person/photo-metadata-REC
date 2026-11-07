"""执行修复：备份 -> 写 EXIF -> 写文件时间 -> 校验。"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime

from . import dates, exif as exifmod, reader, scanner, sniff

BACKUP_DIR = ".pmfix_backup"


@dataclass
class Result:
    path: str
    ok: bool
    action: str
    message: str = ""
    backup: str = ""


def backup_path(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    return os.path.join(root, BACKUP_DIR, rel)


def make_backup(root: str, path: str) -> str:
    dst = backup_path(root, path)
    if os.path.exists(dst):
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.copy2(path, dst)
    except OSError:
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(path, dst)
        except OSError as exc:
            raise OSError(f"备份失败: {exc}") from exc
    return dst


def repair_row(root: str, row: scanner.Row, write_exif: bool = True,
               write_fs: bool = True, backup: bool = True,
               offset: str | None = None) -> Result:
    dt = row.target_dt
    if dt is None:
        return Result(row.path, False, "跳过", "没有可用的目标时间")

    actions = []
    backup_file = ""
    if backup and (write_exif or write_fs):
        try:
            backup_file = make_backup(root, row.path)
        except OSError as exc:
            return Result(row.path, False, "备份", str(exc))

    # ---- 1. EXIF
    if write_exif and row.needs_exif:
        if not row.probe.writable:
            actions.append("仅文件时间")
        elif row.probe.tiff is None and row.probe.has_exif_block:
            actions.append("EXIF 损坏，跳过写入")
        else:
            try:
                _write_exif(row, dt, offset)
                actions.append("已写 EXIF")
            except Exception as exc:
                actions.append(f"EXIF 写入失败: {exc}")

    # ---- 2. 文件时间
    if write_fs:
        before = _mtime(row.path)
        dates.set_file_times(row.path, dt, offset)
        after = _mtime(row.path)
        if after is not None and before != after:
            actions.append("已改文件时间")

    ok = True
    msg = "、".join(actions) if actions else "无需修改"
    if write_exif and row.needs_exif and not row.probe.writable:
        ok = True
    # 校验
    if write_exif and row.needs_exif and row.probe.writable:
        got = _read_back(row.path)
        if got is None or abs((got - dt).total_seconds()) > 1:
            ok = False
            msg += "；校验不一致"
    return Result(row.path, ok, msg if ok else "失败", "" if ok else msg, backup_file)


def _mtime(path: str):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _write_exif(row: scanner.Row, dt: datetime, offset: str | None) -> None:
    tiff = row.probe.tiff
    if tiff is None:
        tiff = exifmod.new_tiff(dt, offset)
    else:
        # 保留原有 GPS / 相机信息，只补时间
        if row.probe.exif_dates.get("datetime_original") == dt and not row.needs_exif:
            return
        tiff.set_dates(dt, offset)
    blob = tiff.to_bytes()
    reader.write_dates(row.path, row.kind, blob)


def _read_back(path: str):
    try:
        p = reader.probe(path)
    except Exception:
        return None
    return p.exif_dates.get("datetime_original")


def apply(root: str, rows: list[scanner.Row], write_exif: bool = True,
          write_fs: bool = True, backup: bool = True, offset: str | None = None,
          progress=None) -> list[Result]:
    results = []
    todo = [r for r in rows if r.selected]
    total = len(todo)
    for i, row in enumerate(todo):
        if progress:
            progress(i, total, row.name)
        try:
            res = repair_row(root, row, write_exif, write_fs, backup, offset)
        except Exception as exc:  # 单个文件失败不影响其它
            res = Result(row.path, False, "异常", str(exc))
        row.note = res.message
        if res.ok and res.action != "无需修改":
            if row.status in (scanner.ST_FIX, scanner.ST_LOW, scanner.ST_PARTIAL):
                row.status = scanner.ST_OK
        results.append(res)
    if progress:
        progress(total, total, "")
    return results


def restore(root: str, rows: list[scanner.Row] | None = None, progress=None) -> list[Result]:
    """从 .pmfix_backup 还原。"""
    base = os.path.join(root, BACKUP_DIR)
    out: list[Result] = []
    if not os.path.isdir(base):
        return out
    targets = []
    for dirpath, _dirnames, filenames in os.walk(base):
        for fn in filenames:
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, base)
            targets.append((src, os.path.join(root, rel)))
    for i, (src, dst) in enumerate(targets):
        if progress:
            progress(i, len(targets), os.path.basename(dst))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            out.append(Result(dst, True, "已还原", "", src))
        except OSError as exc:
            out.append(Result(dst, False, "还原失败", str(exc), src))
    return out


def cleanup_backup(root: str) -> bool:
    base = os.path.join(root, BACKUP_DIR)
    if os.path.isdir(base):
        try:
            shutil.rmtree(base)
            return True
        except OSError:
            return False
    return False
