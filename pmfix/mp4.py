"""MOV / MP4（含 Live Photo 视频）时间读取。

只读不写：视频时间通常随照片一起丢失，写入风险高，本工具只把它当作
「同一时刻的旁证」来给照片定时间，并只改文件系统时间。
"""

from __future__ import annotations

import re
import struct
from datetime import datetime, timedelta, timezone

from .boxes import iter_boxes

MP4_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)

_CONTAINERS = {
    b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"ilst",
    b"edts", b"dinf", b"moof", b"traf", b"mvex",
}


class Mp4Error(Exception):
    pass


def _walk(data, start, end, depth=0):
    """遍历所有盒子，产出 (path_tuple, type, payload_start, payload_end)。"""
    if depth > 8:
        return
    for btype, ps, pe, _bs in iter_boxes(data, start, end):
        path_here = (btype,)
        yield path_here, btype, ps, pe, depth
        if btype in _CONTAINERS:
            yield from _walk(data, ps, pe, depth + 1)
        elif btype == b"meta" and pe - ps >= 4:
            # FullBox：跳过 version/flags
            yield from _walk(data, ps + 4, pe, depth + 1)


def _parse_qt_time(raw: int):
    try:
        return MP4_EPOCH + timedelta(seconds=raw)
    except (OverflowError, ValueError):
        return None


def _parse_day_string(s: str):
    s = s.strip().rstrip("\x00")
    if not s:
        return None, None
    m = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?"
        r"(Z|[+-]\d{2}:?\d{2})?$",
        s,
    )
    if not m:
        m2 = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
        if not m2:
            return None, None
        try:
            return datetime(int(m2.group(1)), int(m2.group(2)), int(m2.group(3))), None
        except ValueError:
            return None, None
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                      int(m.group(4)), int(m.group(5)), int(m.group(6)))
    except ValueError:
        return None, None
    tz = m.group(7)
    offset = None
    if tz and tz != "Z":
        tz = tz.replace(":", "")
        offset = tz[:3] + ":" + tz[3:]
    elif tz == "Z":
        offset = "+00:00"
    return dt, offset


def read_dates(data: bytes) -> list[dict]:
    out: list[dict] = []
    if len(data) < 16:
        return out
    # 找 moov
    moov = None
    for btype, ps, pe, _bs in iter_boxes(data, 0, len(data)):
        if btype == b"moov":
            moov = (ps, pe)
            break
    if not moov:
        return out

    for _path, btype, ps, pe, depth in _walk(data, moov[0], moov[1]):
        if btype == b"mvhd":
            if ps + 4 > pe:
                continue
            ver = data[ps]
            try:
                if ver == 1:
                    raw = struct.unpack_from(">Q", data, ps + 4)[0]
                else:
                    raw = struct.unpack_from(">I", data, ps + 4)[0]
            except struct.error:
                continue
            if raw == 0:
                continue
            dt = _parse_qt_time(raw)
            if dt is None:
                continue
            out.append({
                "dt_local": dt.astimezone().replace(tzinfo=None),
                "offset": None,
                "source": "mp4:mvhd",
                "path": "moov/mvhd",
                "note": "QuickTime 创建时间（UTC，已换算本机时区）",
                "weight": 55,
            })
        elif btype in (b"\xa9day", b"\xa9DAY"):
            # ilst 里的 ©day：payload 是 data box（前 8 字节是类型/语言标记）
            payload = data[ps:pe]
            text = None
            for db, dps, dpe, _ in iter_boxes(payload, 0, len(payload)):
                if db == b"data":
                    body = payload[dps:dpe]
                    text = body[8:] if len(body) >= 8 else body
                    break
            if text is None:
                text = payload
            try:
                s = text.decode("utf-8", "replace")
            except Exception:  # pragma: no cover
                continue
            dt, off = _parse_day_string(s)
            if dt is None:
                continue
            out.append({
                "dt_local": dt,
                "offset": off,
                "source": "mp4:©day",
                "path": "moov/udta/meta/ilst/©day",
                "note": "视频内嵌拍摄日期",
                "weight": 80,
            })
    # 去掉重复项（同名同值）
    seen = set()
    uniq = []
    for item in out:
        key = (item["source"], item["dt_local"], item["offset"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq
