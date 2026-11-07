"""目录扫描 + 时间推断 + 置信度评分。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

from . import dates, reader, sniff

# 状态
ST_OK = "ok"            # EXIF 已有拍摄时间
ST_FIX = "fix"          # EXIF 缺失，有高置信来源
ST_LOW = "low"          # EXIF 缺失，只有弱线索
ST_NONE = "none"        # 完全推断不出来
ST_PARTIAL = "partial"  # 有 Digitized/DateTime 但缺 DateTimeOriginal
ST_VIDEO = "video"      # 视频：只能改文件时间
ST_SKIP = "skip"        # 不处理

STATUS_TEXT = {
    ST_OK: "已有拍摄时间",
    ST_FIX: "缺失 · 可修复",
    ST_LOW: "缺失 · 低置信",
    ST_NONE: "缺失 · 无法推断",
    ST_PARTIAL: "不完整",
    ST_VIDEO: "视频",
    ST_SKIP: "跳过",
}

# 置信度区间
HIGH = 60
MID = 30


@dataclass
class Row:
    path: str
    rel: str
    kind: str
    size: int
    probe: reader.Probe
    candidates: list[dict] = field(default_factory=list)
    chosen: dict | None = None
    manual: dict | None = None
    status: str = ST_NONE
    note: str = ""
    selected: bool = True

    # ---- 展示辅助
    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def exif_dt(self) -> datetime | None:
        return self.probe.exif_dates.get("best")

    @property
    def target_dt(self) -> datetime | None:
        src = self.manual or self.chosen
        return src["dt_local"] if src else None

    @property
    def target_offset(self) -> str | None:
        src = self.manual or self.chosen
        return src.get("offset") if src else None

    @property
    def confidence(self) -> int:
        src = self.manual or self.chosen
        if not src:
            return 0
        return 100 if self.manual else int(src.get("weight", 0))

    @property
    def source_label(self) -> str:
        src = self.manual or self.chosen
        if not src:
            return "—"
        if self.manual:
            return "手动指定"
        return _pretty_source(src)

    @property
    def needs_exif(self) -> bool:
        # 手动指定时间时，即使原本已有拍摄时间也要改写
        if self.manual is not None:
            return True
        return self.status in (ST_FIX, ST_LOW, ST_PARTIAL)

    @property
    def needs_fs(self) -> bool:
        dt = self.target_dt
        if dt is None:
            return False
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(self.path))
        except OSError:
            return False
        return abs((mtime - dt).total_seconds()) > 2

    @property
    def writable(self) -> bool:
        return self.probe.writable


def _pretty_source(src: dict) -> str:
    s = src.get("source", "")
    table = {
        "exif:DateTimeOriginal": "EXIF 拍摄时间",
        "exif:DateTimeDigitized": "EXIF 数字化时间",
        "exif:DateTime": "EXIF 修改时间",
        "exif:GPSDateTime": "EXIF GPS 时间",
        "filename": "文件名",
        "folder": "目录名",
        "fs:mtime": "文件修改时间",
        "fs:ctime": "文件创建时间",
        "video:shared": "同组视频",
        "sibling": "同组文件",
    }
    if s in table:
        return table[s]
    if s.startswith("xmp:") or s == "xmp:CreateDate":
        return "XMP 元数据"
    if s.startswith("iptc:"):
        return "IPTC 元数据"
    if s.startswith("mp4:"):
        return "视频内嵌时间"
    return s or "未知"


def _exif_candidates(probe: reader.Probe) -> list[dict]:
    out = []
    d = probe.exif_dates or {}
    if d.get("datetime_original"):
        out.append({"dt_local": d["datetime_original"], "offset": d.get("offset_original"),
                    "source": "exif:DateTimeOriginal", "note": "EXIF 原始拍摄时间", "weight": 100})
    if d.get("datetime_digitized"):
        out.append({"dt_local": d["datetime_digitized"], "offset": d.get("offset_digitized"),
                    "source": "exif:DateTimeDigitized", "note": "EXIF 数字化时间", "weight": 92})
    if d.get("datetime"):
        out.append({"dt_local": d["datetime"], "offset": d.get("offset"),
                    "source": "exif:DateTime", "note": "EXIF 修改时间", "weight": 55})
    gps = d.get("gps_datetime")
    if gps:
        out.append({"dt_local": gps, "offset": "+00:00",
                    "source": "exif:GPSDateTime", "note": "GPS UTC 时间", "weight": 70})
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for it in items:
        key = (it.get("source"), it.get("dt_local"), it.get("offset"))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def walk_files(root: str, recursive: bool = True, skip_names=()):
    skip = {".pmfix_backup", "$RECYCLE.BIN", "System Volume Information",
            ".git", ".svn", "__pycache__", "node_modules"} | set(skip_names)
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext in sniff.SIDECAR_EXTS and ext != ".xmp":
                    continue
                yield os.path.join(dirpath, fn)
    else:
        try:
            names = os.listdir(root)
        except OSError:
            return
        for fn in names:
            full = os.path.join(root, fn)
            if os.path.isfile(full) and not fn.startswith("."):
                yield full


def scan(root: str, recursive: bool = True, include_video: bool = True,
         progress=None) -> list[Row]:
    root = os.path.abspath(root)
    files = list(walk_files(root, recursive))
    rows: list[Row] = []
    total = len(files)
    for idx, path in enumerate(files):
        if progress:
            progress(idx, total, path)
        if not sniff.is_media(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        if not include_video and ext in sniff.VIDEO_EXTS:
            continue
        probe = reader.probe(path)
        if probe.kind == sniff.KIND_OTHER:
            continue
        rel = os.path.relpath(path, root)
        row = Row(path=path, rel=rel, kind=probe.kind, size=probe.size, probe=probe)
        row.candidates = _exif_candidates(probe)
        row.candidates.extend(probe.extra_dates)
        row.candidates.extend(dates.parse_filename(row.name))
        row.candidates.extend(dates.fs_candidates(path))
        rows.append(row)

    _propagate_groups(rows)
    _propagate_folder(rows)

    for row in rows:
        _finalize(row)
    if progress:
        progress(total, total, "")
    return rows


def _propagate_groups(rows: list[Row]) -> None:
    """Live Photo（IMG_1234.HEIC + IMG_1234.MOV）等共用主文件名的组，互相对齐。"""
    groups: dict[tuple, list[Row]] = {}
    for row in rows:
        key = (os.path.dirname(row.path).lower(),
               os.path.splitext(row.name)[0].lower())
        groups.setdefault(key, []).append(row)
    for _key, members in groups.items():
        if len(members) < 2:
            continue
        strong = []
        for m in members:
            for c in m.candidates:
                if c["weight"] >= HIGH:
                    strong.append(c)
        if not strong:
            continue
        strong.sort(key=lambda c: -c["weight"])
        best = strong[0]
        for m in members:
            if any(c["weight"] >= HIGH for c in m.candidates):
                continue
            m.candidates.append({
                "dt_local": best["dt_local"],
                "offset": best.get("offset"),
                "source": "sibling",
                "note": "与同组文件（Live Photo / 同名文件）一致",
                "weight": 82,
            })


def _propagate_folder(rows: list[Row]) -> None:
    folder_cache: dict[str, list[dict]] = {}
    for row in rows:
        if any(c["weight"] >= MID for c in row.candidates):
            continue
        parent = os.path.dirname(row.path)
        if parent not in folder_cache:
            folder_cache[parent] = dates.parse_folder(row.path)
        for c in folder_cache[parent]:
            row.candidates.append(dict(c))


def _finalize(row: Row) -> None:
    row.candidates = _dedupe(row.candidates)
    row.candidates.sort(key=lambda c: (-c["weight"], c.get("source", "")))
    row.chosen = row.candidates[0] if row.candidates else None

    d = row.probe.exif_dates or {}
    if row.kind == sniff.KIND_MP4:
        row.status = ST_VIDEO
        row.selected = bool(row.candidates)
        if not row.candidates:
            row.status = ST_NONE
            row.selected = False
        return

    if d.get("datetime_original"):
        row.status = ST_OK
        row.selected = False
        return
    if d.get("datetime_digitized") or d.get("datetime"):
        row.status = ST_PARTIAL
        row.selected = True
        return
    if not row.candidates:
        row.status = ST_NONE
        row.selected = False
        return
    best = row.candidates[0]
    if best["weight"] >= HIGH:
        row.status = ST_FIX
        row.selected = True
    elif best["weight"] >= MID:
        row.status = ST_LOW
        row.selected = True
    else:
        row.status = ST_NONE
        row.selected = False

    if not row.probe.writable:
        row.note = "该格式不支持写入 EXIF，只能改文件时间"
    if row.probe.error:
        row.note = row.probe.error


def summarize(rows: list[Row]) -> dict:
    stats = {s: 0 for s in STATUS_TEXT}
    for r in rows:
        stats[r.status] = stats.get(r.status, 0) + 1
    stats["total"] = len(rows)
    stats["selected"] = sum(1 for r in rows if r.selected)
    return stats


def suspicious_uniform(rows: list[Row]) -> str:
    """所有文件都被写入了同一时间 -> 多半是批量工具干的好事。"""
    times = [r.exif_dt for r in rows if r.exif_dt]
    if len(times) < 5:
        return ""
    first = times[0]
    same = sum(1 for t in times if t == first)
    if same / len(times) > 0.9:
        return f"超过 90% 的文件拍摄时间完全相同（{first:%Y-%m-%d %H:%M:%S}），疑似被批量改写"
    return ""
