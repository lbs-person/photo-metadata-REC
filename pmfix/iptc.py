"""IPTC-IIM（通常位于 JPEG 的 APP13「Photoshop 3.0」段）日期读取。

iCloud 剥离 EXIF 时，少数情况下 IPTC 会残留 —— 这是一条有价值的旁证。
"""

from __future__ import annotations

import re
import struct
from datetime import datetime

PHOTOSHOP_HEADER = b"Photoshop 3.0\x00"
IPTC_RESOURCE_ID = 0x0404


def extract_iptc(app13: bytes) -> bytes:
    """从 APP13 负载中取出 IPTC-NAA 资源块。"""
    if not app13.startswith(PHOTOSHOP_HEADER):
        return b""
    p = len(PHOTOSHOP_HEADER)
    n = len(app13)
    while p + 12 <= n:
        if app13[p:p + 4] != b"8BIM":
            # 有些写入器用其它签名，逐个字节找
            nxt = app13.find(b"8BIM", p + 1)
            if nxt < 0:
                return b""
            p = nxt
        res_id = struct.unpack_from(">H", app13, p + 4)[0]
        name_len = app13[p + 6]
        q = p + 7 + name_len
        if q % 2 != (p + 6) % 2:  # 名称需补齐到偶数
            q += 1
        if q + 4 > n:
            return b""
        size = struct.unpack_from(">I", app13, q)[0]
        q += 4
        if size > n - q:
            size = n - q
        data = app13[q:q + size]
        if res_id == IPTC_RESOURCE_ID:
            return data
        p = q + size + (size & 1)
    return b""


def parse_records(data: bytes):
    """产出 (record, dataset, value_bytes)。"""
    p = 0
    n = len(data)
    while p + 5 <= n:
        if data[p] != 0x1C:
            p += 1
            continue
        rec = data[p + 1]
        ds = data[p + 2]
        length = struct.unpack_from(">H", data, p + 3)[0]
        p += 5
        if length & 0x8000:
            cnt = length & 0x7FFF
            if cnt > 4 or p + cnt > n:
                return
            length = int.from_bytes(data[p:p + cnt], "big")
            p += cnt
        if p + length > n:
            return
        yield rec, ds, data[p:p + length]
        p += length


def parse_iptc_dates(data: bytes) -> list[dict]:
    if not data:
        return []
    date_val = None
    time_val = None
    for rec, ds, val in parse_records(data):
        if rec != 2:
            continue
        if ds == 55:      # DateCreated  YYYYMMDD
            date_val = val.decode("latin-1", "replace").strip()
        elif ds == 60:    # TimeCreated  HHMMSS±HHMM
            time_val = val.decode("latin-1", "replace").strip()
        elif ds == 62:    # DigitalCreationDate
            if not date_val:
                date_val = val.decode("latin-1", "replace").strip()
        elif ds == 63:
            if not time_val:
                time_val = val.decode("latin-1", "replace").strip()
    if not date_val:
        return []
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", date_val)
    if not m:
        return []
    hh = mm = ss = 0
    offset = None
    if time_val:
        mt = re.match(r"^(\d{2})(\d{2})(\d{2})?\s*([+-]\d{2}:?\d{2})?$", time_val)
        if mt:
            hh, mm = int(mt.group(1)), int(mt.group(2))
            ss = int(mt.group(3) or 0)
            tz = mt.group(4)
            if tz:
                tz = tz.replace(":", "")
                offset = tz[:3] + ":" + tz[3:]
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), hh, mm, ss)
    except ValueError:
        return []
    return [{
        "dt_local": dt,
        "offset": offset,
        "source": "iptc:DateCreated",
        "note": "IPTC 元数据",
        "weight": 78,
    }]
