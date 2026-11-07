"""从文件名 / 目录名 / 文件系统时间推断日期。"""

from __future__ import annotations

import os
import re
from datetime import datetime

# (正则, 描述, 权重)。只取第一个匹配，避免同一个文件名命中多条。
_TIME_PATTERNS = [
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})[.\-:：](\d{2})[.\-:：](\d{2})(?!\d)"), "文件名含日期时间", 68),
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})[ _\-T](\d{2})(\d{2})(\d{2})\d{3}(?!\d)"), "文件名含日期时间(毫秒)", 66),
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})[ _\-T](\d{2})(\d{2})(\d{2})(?!\d)"), "文件名含日期时间(紧凑)", 66),
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)"), "文件名含 14 位时间戳", 64),
    (re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日[ _]?(\d{1,2})[时:：](\d{1,2})[分:：]?(\d{1,2})?"), "文件名含中文日期", 60),
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})(?!\d)"), "文件名含日期时间(短横线)", 60),
]

_DATE_PATTERNS = [
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), "文件名含日期", 48),
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"), "文件名含日期(紧凑)", 42),
]

_EPOCH_RE = re.compile(r"(?<!\d)(1[3-9]\d{8}|1[3-9]\d{11})(?!\d)")


def _make(y, mo, d, h=0, mi=0, s=0):
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except (ValueError, TypeError):
        return None
    if not (1990 <= dt.year <= 2100):
        return None
    return dt


def _from_groups(m, has_time: bool):
    g = m.groups()
    if has_time:
        y, mo, d, h, mi = g[0], g[1], g[2], g[3], g[4]
        s = g[5] if len(g) > 5 and g[5] else 0
        return _make(y, mo, d, h, mi, s)
    return _make(g[0], g[1], g[2])


def parse_filename(name: str) -> list[dict]:
    stem = os.path.splitext(name)[0]
    out = []
    for regex, label, weight in _TIME_PATTERNS:
        m = regex.search(stem)
        if m:
            dt = _from_groups(m, True)
            if dt:
                out.append({"dt_local": dt, "offset": None, "source": "filename",
                            "note": label, "weight": weight, "matched": m.group(0)})
                return out
    for regex, label, weight in _DATE_PATTERNS:
        m = regex.search(stem)
        if m:
            # 纯日期要排除文件名里带序号的情况（如 20230101_001）
            dt = _from_groups(m, False)
            if dt:
                out.append({"dt_local": dt, "offset": None, "source": "filename",
                            "note": label + "（无时分秒）", "weight": weight,
                            "matched": m.group(0)})
                return out
    m = _EPOCH_RE.search(stem)
    if m:
        raw = m.group(1)
        try:
            ts = int(raw) / (1000.0 if len(raw) == 13 else 1.0)
            dt = datetime.fromtimestamp(ts)
        except (ValueError, OSError, OverflowError):
            dt = None
        if dt and 1990 <= dt.year <= 2100:
            out.append({"dt_local": dt, "offset": None, "source": "filename",
                        "note": "文件名含 Unix 时间戳", "weight": 45, "matched": raw})
    return out


def parse_folder(path: str, depth: int = 2) -> list[dict]:
    """从上层目录名里找日期（相册常常按「2021-08-xx」命名）。"""
    out = []
    cur = os.path.dirname(os.path.abspath(path))
    for _ in range(depth):
        if not cur or os.path.dirname(cur) == cur:
            break
        name = os.path.basename(cur)
        for regex, label, weight in _DATE_PATTERNS:
            m = regex.search(name)
            if m:
                dt = _from_groups(m, False)
                if dt:
                    out.append({"dt_local": dt, "offset": None, "source": "folder",
                                "note": f"目录名：{label}", "weight": max(20, weight - 20),
                                "matched": name})
                    return out
        cur = os.path.dirname(cur)
    return out


def fs_candidates(path: str) -> list[dict]:
    try:
        st = os.stat(path)
    except OSError:
        return []
    out = []
    try:
        out.append({"dt_local": datetime.fromtimestamp(st.st_mtime), "offset": None,
                    "source": "fs:mtime", "note": "文件修改时间（下载/解压时间）",
                    "weight": 15})
    except (ValueError, OSError, OverflowError):
        pass
    try:
        birth = getattr(st, "st_birthtime", None)
    except OSError:  # pragma: no cover
        birth = None
    if birth:
        try:
            b = datetime.fromtimestamp(birth)
            same = bool(out) and abs((b - out[0]["dt_local"]).total_seconds()) <= 2
            if not same:
                out.append({"dt_local": b, "offset": None, "source": "fs:ctime",
                            "note": "文件创建时间", "weight": 12})
        except (ValueError, OSError, OverflowError):
            pass
    return out


def set_file_times(path: str, dt: datetime, offset=None) -> None:
    """把文件修改 / 访问时间设为 dt。

    offset 为 "+08:00" 这类偏移时，dt 视为该时区的本地时间；
    offset 为 None 时按本机本地时间解释。
    """
    import calendar
    import time as _time
    if offset:
        sign = 1 if offset[0] == "+" else -1
        hh, mm = int(offset[1:3]), int(offset[4:6])
        epoch = calendar.timegm(dt.timetuple()) - sign * (hh * 3600 + mm * 60)
    else:
        epoch = _time.mktime(dt.timetuple())
    try:
        os.utime(path, (epoch, epoch))
    except (OSError, OverflowError):
        pass
