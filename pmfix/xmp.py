"""XMP 里的日期字段（APP1 XMP / PNG iTXt / 独立 .xmp 边车）。"""

from __future__ import annotations

import re
from datetime import datetime

_FIELD_RE = re.compile(
    r"<(?:\w+:)?(CreateDate|DateTimeOriginal|DateCreated|ModifyDate|DateTimeDigitized)>"
    r"\s*([^<]*)</", re.I)
_ATTR_RE = re.compile(
    r"(?:xmp:CreateDate|xmp:ModifyDate|photoshop:DateCreated|exif:DateTimeOriginal)"
    r"\s*=\s*[\"']([^\"']+)[\"']", re.I)

_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.(\d+))?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?$")


def parse_iso(text: str):
    s = (text or "").strip()
    m = _ISO_RE.match(s)
    if not m:
        d = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
        if not d:
            return None, None
        try:
            return datetime(int(d.group(1)), int(d.group(2)), int(d.group(3))), None
        except ValueError:
            return None, None
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                      int(m.group(4)), int(m.group(5)), int(m.group(6) or 0),
                      int((m.group(7) or "0")[:6].ljust(6, "0")))
    except ValueError:
        return None, None
    tz = m.group(8)
    offset = None
    if tz == "Z":
        offset = "+00:00"
    elif tz:
        tz = tz.replace(":", "")
        offset = tz[:3] + ":" + tz[3:]
    return dt, offset


def parse_xmp_dates(data: bytes) -> list[dict]:
    if not data:
        return []
    try:
        text = data.decode("utf-8", "replace")
    except Exception:  # pragma: no cover
        return []
    found: list[tuple[str, str]] = []
    for m in _FIELD_RE.finditer(text):
        found.append((m.group(1), m.group(2)))
    for m in _ATTR_RE.finditer(text):
        found.append(("CreateDate", m.group(1)))

    weight = {"DateTimeOriginal": 88, "CreateDate": 85, "DateCreated": 84,
              "DateTimeDigitized": 80, "ModifyDate": 60}
    out = []
    seen = set()
    for name, value in found:
        dt, offset = parse_iso(value)
        if dt is None:
            continue
        key = (name, dt, offset)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "dt_local": dt,
            "offset": offset,
            "source": f"xmp:{name}",
            "note": "XMP 元数据",
            "weight": weight.get(name, 60),
        })
    return out
