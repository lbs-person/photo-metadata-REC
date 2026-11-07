"""EXIF / TIFF 读写引擎。

设计要点
--------
* 只解析真正需要的部分：IFD0、ExifIFD、GPS IFD、Interop IFD、IFD1(缩略图)。
* 两种写入路径：
    1. 就地修补（in-place）：目标标签已存在且长度够 -> 直接改字节，零风险，
       MakerNote、缩略图、未知标签全部原样保留。
    2. 重建（rebuild）：目标标签缺失 -> 按规范重新排布 IFD 后整体序列化。
       重建时会保留 MakerNote 原始字节，但其内部绝对偏移可能失效（见 README）。
* 所有长度、偏移都做越界检查，损坏文件抛 ExifError 而不是崩溃。
"""

from __future__ import annotations

import re
import struct
from datetime import datetime

# ---------------------------------------------------------------- 常量

T_BYTE = 1
T_ASCII = 2
T_SHORT = 3
T_LONG = 4
T_RATIONAL = 5
T_SBYTE = 6
T_UNDEFINED = 7
T_SSHORT = 8
T_SLONG = 9
T_SRATIONAL = 10
T_FLOAT = 11
T_DOUBLE = 12
T_IFD = 13

TYPE_SIZE = {
    T_BYTE: 1, T_ASCII: 1, T_SHORT: 2, T_LONG: 4, T_RATIONAL: 8,
    T_SBYTE: 1, T_UNDEFINED: 1, T_SSHORT: 2, T_SLONG: 4, T_SRATIONAL: 8,
    T_FLOAT: 4, T_DOUBLE: 8, T_IFD: 4,
}

# 标签
TAG_IMAGE_WIDTH = 0x0100
TAG_IMAGE_LENGTH = 0x0101
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_ORIENTATION = 0x0112
TAG_XRES = 0x011A
TAG_YRES = 0x011B
TAG_RESUNIT = 0x0128
TAG_SOFTWARE = 0x0131
TAG_MODIFY_DATE = 0x0132          # DateTime
TAG_ARTIST = 0x013B
TAG_YCBCR_POS = 0x0213
TAG_THUMB_OFFSET = 0x0201
TAG_THUMB_LENGTH = 0x0202
TAG_COPYRIGHT = 0x8298
TAG_EXIF_IFD = 0x8769
TAG_GPS_IFD = 0x8825
TAG_EXPOSURE = 0x829A
TAG_FNUMBER = 0x829D
TAG_ISO = 0x8827
TAG_EXIF_VERSION = 0x9000
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_OFFSET_TIME = 0x9010
TAG_OFFSET_TIME_ORIGINAL = 0x9011
TAG_OFFSET_TIME_DIGITIZED = 0x9012
TAG_SUBSEC_TIME = 0x9290
TAG_SUBSEC_TIME_ORIGINAL = 0x9291
TAG_SUBSEC_TIME_DIGITIZED = 0x9292
TAG_USER_COMMENT = 0x9286
TAG_FLASH = 0x9209
TAG_FOCAL_LENGTH = 0x920A
TAG_MAKER_NOTE = 0x927C
TAG_INTEROP_IFD = 0xA005
TAG_PIXEL_X = 0xA002
TAG_PIXEL_Y = 0xA003
TAG_LENS_MAKE = 0xA433
TAG_LENS_MODEL = 0xA434

TAG_GPS_VERSION = 0x0000
TAG_GPS_LAT_REF = 0x0001
TAG_GPS_LAT = 0x0002
TAG_GPS_LON_REF = 0x0003
TAG_GPS_LON = 0x0004
TAG_GPS_ALT_REF = 0x0005
TAG_GPS_ALT = 0x0006
TAG_GPS_TIME_STAMP = 0x0007
TAG_GPS_DATE_STAMP = 0x001D

EXIF_HEADER = b"Exif\x00\x00"
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"

_IFD_NAMES = ("ifd0", "exif", "gps", "interop", "ifd1")


class ExifError(Exception):
    """EXIF 结构损坏或无法解析。"""


# ---------------------------------------------------------------- 值编码

def enc_ascii(value: str) -> bytes:
    b = value.encode("utf-8", "replace")
    if not b.endswith(b"\x00"):
        b += b"\x00"
    return b


def enc_short(*vals) -> bytes:
    return b"".join(struct.pack("<H", int(v) & 0xFFFF) for v in vals)


def enc_long(*vals) -> bytes:
    return b"".join(struct.pack("<I", int(v) & 0xFFFFFFFF) for v in vals)


def enc_rational(*pairs) -> bytes:
    out = []
    for num, den in pairs:
        out.append(struct.pack("<II", int(num), int(den) or 1))
    return b"".join(out)


def entry_ascii(tag: int, value: str) -> "Entry":
    raw = enc_ascii(value)
    return Entry(tag, T_ASCII, len(raw), raw)


def entry_short(tag: int, *vals) -> "Entry":
    return Entry(tag, T_SHORT, len(vals), enc_short(*vals))


def entry_long(tag: int, *vals) -> "Entry":
    return Entry(tag, T_LONG, len(vals), enc_long(*vals))


# ---------------------------------------------------------------- 条目

class Entry:
    __slots__ = ("tag", "typ", "count", "raw")

    def __init__(self, tag: int, typ: int, count: int, raw: bytes):
        self.tag = int(tag)
        self.typ = int(typ)
        self.count = int(count)
        self.raw = bytes(raw)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Entry 0x{self.tag:04X} t={self.typ} n={self.count} {self.raw[:24]!r}>"

    def copy(self) -> "Entry":
        return Entry(self.tag, self.typ, self.count, self.raw)

    @property
    def byte_size(self) -> int:
        return TYPE_SIZE.get(self.typ, 1) * self.count

    # -- 解码
    def text(self) -> str:
        if self.typ in (T_UNDEFINED, T_BYTE):
            b = self.raw.split(b"\x00")[0]
        else:
            b = self.raw
            nul = b.find(b"\x00")
            if nul >= 0:
                b = b[:nul]
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1", "replace")

    def ints(self, endian: str = "II") -> list:
        e = "<" if endian == "II" else ">"
        if self.typ in (T_SHORT, T_SSHORT):
            fmt = e + ("H" if self.typ == T_SHORT else "h")
            n = min(self.count, len(self.raw) // 2)
            return list(struct.unpack_from(fmt * n, self.raw, 0))
        if self.typ in (T_LONG, T_SLONG, T_IFD):
            fmt = e + ("I" if self.typ != T_SLONG else "i")
            n = min(self.count, len(self.raw) // 4)
            return list(struct.unpack_from(fmt * n, self.raw, 0))
        if self.typ in (T_BYTE, T_SBYTE, T_UNDEFINED):
            return list(self.raw)
        return []

    def rationals(self, endian: str = "II") -> list:
        e = "<" if endian == "II" else ">"
        tag = "I" if self.typ == T_RATIONAL else "i"
        out = []
        n = min(self.count, len(self.raw) // 8)
        for i in range(n):
            num, den = struct.unpack_from(e + tag + tag, self.raw, i * 8)
            out.append((num, den))
        return out

    def first_int(self, endian: str = "II", default=0) -> int:
        vals = self.ints(endian)
        return vals[0] if vals else default


def _decode_datetime_string(s: str):
    """把 EXIF 时间字符串解析成 datetime；非法返回 None。"""
    if not s:
        return None
    s = s.strip().strip("\x00").strip()
    if not s:
        return None
    m = re.match(
        r"^(\d{4})[:\-/\.](\d{1,2})[:\-/\.](\d{1,2})[ T_](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.(\d+))?",
        s,
    )
    if not m:
        return None
    try:
        year, mon, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour, minute = int(m.group(4)), int(m.group(5))
        sec = int(m.group(6) or 0)
        micro = int((m.group(7) or "0")[:6].ljust(6, "0"))
        if not (1900 <= year <= 2999 and 1 <= mon <= 12 and 1 <= day <= 31):
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= sec <= 60):
            return None
        return datetime(year, mon, day, hour, minute, min(sec, 59), micro)
    except ValueError:
        return None


def format_exif_datetime(dt: datetime) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def _norm_offset(raw) -> str | None:
    if not raw:
        return None
    raw = str(raw).strip().strip("\x00")
    if not raw or raw in ("+00:00", "-00:00") and False:
        return None
    if re.fullmatch(r"[+-]\d{2}:\d{2}", raw):
        return raw
    if re.fullmatch(r"[+-]\d{2}\d{2}", raw):
        return raw[:3] + ":" + raw[3:]
    return None


# ---------------------------------------------------------------- TIFF

class Tiff:
    """一个 TIFF/EXIF 结构的轻量内存模型。"""

    def __init__(self, endian: str = "II"):
        self.endian = endian if endian in ("II", "MM") else "II"
        self.ifd0: dict[int, Entry] = {}
        self.exif: dict[int, Entry] = {}
        self.gps: dict[int, Entry] = {}
        self.interop: dict[int, Entry] = {}
        self.ifd1: dict[int, Entry] = {}
        self.thumb: bytes = b""
        self.raw: bytes = b""
        # 只读调试信息
        self.notes: list[str] = []

    # -- 访问
    def group(self, name: str) -> dict[int, Entry]:
        return getattr(self, name)

    def get(self, group: str, tag: int) -> Entry | None:
        return getattr(self, group).get(tag)

    def put(self, group: str, entry: Entry) -> None:
        getattr(self, group)[entry.tag] = entry

    def drop(self, group: str, tag: int) -> None:
        getattr(self, group).pop(tag, None)

    def group_of_tag(self, tag: int) -> str | None:
        for name in ("ifd0", "exif", "gps"):
            if tag in getattr(self, name):
                return name
        return None

    def locate(self, tag: int):
        """返回 (group_name, entry) 或 (None, None)。"""
        for name in ("ifd0", "exif", "gps"):
            ent = getattr(self, name).get(tag)
            if ent is not None:
                return name, ent
        return None, None

    # -- 日期
    def date_info(self) -> dict:
        """汇总所有与拍摄时间相关的字段。"""
        info = {
            "datetime_original": None,
            "datetime_digitized": None,
            "datetime": None,
            "offset_original": None,
            "offset_digitized": None,
            "offset": None,
            "subsec_original": None,
            "gps_datetime": None,
            "make": None,
            "model": None,
        }
        e = self.exif
        if TAG_DATETIME_ORIGINAL in e:
            info["datetime_original"] = _decode_datetime_string(e[TAG_DATETIME_ORIGINAL].text())
        if TAG_DATETIME_DIGITIZED in e:
            info["datetime_digitized"] = _decode_datetime_string(e[TAG_DATETIME_DIGITIZED].text())
        if TAG_MODIFY_DATE in self.ifd0:
            info["datetime"] = _decode_datetime_string(self.ifd0[TAG_MODIFY_DATE].text())
        if TAG_OFFSET_TIME_ORIGINAL in e:
            info["offset_original"] = _norm_offset(e[TAG_OFFSET_TIME_ORIGINAL].text())
        if TAG_OFFSET_TIME_DIGITIZED in e:
            info["offset_digitized"] = _norm_offset(e[TAG_OFFSET_TIME_DIGITIZED].text())
        if TAG_OFFSET_TIME in e:
            info["offset"] = _norm_offset(e[TAG_OFFSET_TIME].text())
        if TAG_SUBSEC_TIME_ORIGINAL in e:
            info["subsec_original"] = e[TAG_SUBSEC_TIME_ORIGINAL].text().strip() or None
        if TAG_MAKE in self.ifd0:
            info["make"] = self.ifd0[TAG_MAKE].text().strip() or None
        if TAG_MODEL in self.ifd0:
            info["model"] = self.ifd0[TAG_MODEL].text().strip() or None
        info["gps_datetime"] = self._gps_datetime()
        dt = info["datetime_original"] or info["datetime_digitized"] or info["datetime"]
        if dt and info["subsec_original"] and info["datetime_original"]:
            frac = info["subsec_original"]
            try:
                micro = int(("0." + frac)[:8].split(".")[1][:6].ljust(6, "0"))
                dt = dt.replace(microsecond=micro)
            except (ValueError, IndexError):
                pass
        info["best"] = dt
        return info

    def _gps_datetime(self):
        d = self.gps.get(TAG_GPS_DATE_STAMP)
        t = self.gps.get(TAG_GPS_TIME_STAMP)
        if d is None or t is None:
            return None
        ds = d.text().strip()
        m = re.match(r"^(\d{4})[:\-/](\d{1,2})[:\-/](\d{1,2})", ds)
        if not m:
            return None
        rats = t.rationals(self.endian)
        if len(rats) < 3:
            return None
        try:
            hh = rats[0][0] / rats[0][1] if rats[0][1] else 0
            mm = rats[1][0] / rats[1][1] if rats[1][1] else 0
            ss = rats[2][0] / rats[2][1] if rats[2][1] else 0
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(hh), int(mm), int(ss),
                            int(round((ss - int(ss)) * 1e6)))
        except (ValueError, ZeroDivisionError, IndexError):
            return None

    def has_any_date(self) -> bool:
        d = self.date_info()
        return bool(d["datetime_original"] or d["datetime_digitized"] or d["datetime"])

    def set_dates(self, dt: datetime, offset: str | None = None) -> None:
        """写入 DateTimeOriginal / DateTimeDigitized / DateTime（就地或新增）。"""
        s = format_exif_datetime(dt)
        self.put("exif", entry_ascii(TAG_DATETIME_ORIGINAL, s))
        self.put("exif", entry_ascii(TAG_DATETIME_DIGITIZED, s))
        if self.ifd0.get(TAG_MODIFY_DATE) is None:
            self.put("ifd0", entry_ascii(TAG_MODIFY_DATE, s))
        if offset:
            self.put("exif", entry_ascii(TAG_OFFSET_TIME_ORIGINAL, offset))
            self.put("exif", entry_ascii(TAG_OFFSET_TIME_DIGITIZED, offset))
            if TAG_OFFSET_TIME in self.exif:
                self.put("exif", entry_ascii(TAG_OFFSET_TIME, offset))
        if TAG_EXIF_VERSION not in self.exif:
            self.put("exif", Entry(TAG_EXIF_VERSION, T_UNDEFINED, 4, b"0231"))
        if TAG_SOFTWARE not in self.ifd0:
            self.put("ifd0", entry_ascii(TAG_SOFTWARE, "PhotoMetaFix"))

    # -- 序列化
    def to_bytes(self) -> bytes:
        return serialize(self)


def _ifd_block_size(n: int) -> int:
    return 2 + 12 * n + 4


def _data_block_size(entries) -> int:
    total = 0
    for ent in entries:
        if ent.byte_size > 4:
            total += ent.byte_size + (ent.byte_size & 1)
    return total


def serialize(tiff: Tiff) -> bytes:
    """按 EXIF 规范重新排布并序列化整棵 TIFF。"""
    e = "<" if tiff.endian == "II" else ">"

    g0 = {t: ent.copy() for t, ent in tiff.ifd0.items()}
    ge = {t: ent.copy() for t, ent in tiff.exif.items()}
    gg = {t: ent.copy() for t, ent in tiff.gps.items()}
    gi = {t: ent.copy() for t, ent in tiff.interop.items()}
    g1 = {t: ent.copy() for t, ent in tiff.ifd1.items()}
    thumb = tiff.thumb or b""

    for g in (g0, ge, gg, gi):
        g.pop(TAG_EXIF_IFD, None)
        g.pop(TAG_GPS_IFD, None)
        g.pop(TAG_INTEROP_IFD, None)
    g1.pop(TAG_THUMB_OFFSET, None)
    g1.pop(TAG_THUMB_LENGTH, None)
    if not thumb or not g1:
        g1 = {}
        thumb = b""

    # 指针
    if ge or gg:
        g0[TAG_EXIF_IFD] = entry_long(TAG_EXIF_IFD, 0)
    if gg:
        g0[TAG_GPS_IFD] = entry_long(TAG_GPS_IFD, 0)
    if gi:
        ge[TAG_INTEROP_IFD] = entry_long(TAG_INTEROP_IFD, 0)

    if not g0 and not ge:
        raise ExifError("无可写内容")

    if thumb:
        g1[TAG_THUMB_OFFSET] = entry_long(TAG_THUMB_OFFSET, 0)
        g1[TAG_THUMB_LENGTH] = entry_long(TAG_THUMB_LENGTH, 0)

    lists = {k: list(v.values()) for k, v in
             (("ifd0", g0), ("exif", ge), ("gps", gg), ("interop", gi), ("ifd1", g1))}

    # 布局顺序：ifd0(+data) exif(+data) interop(+data) gps(+data) ifd1(+data) thumb
    order = ["ifd0", "exif", "interop", "gps", "ifd1"]
    offsets: dict[str, int] = {}
    data_offsets: dict[str, int] = {}
    pos = 8
    for name in order:
        items = lists[name]
        if name != "ifd0" and not items:
            offsets[name] = 0
            data_offsets[name] = pos
            continue
        offsets[name] = pos
        pos += _ifd_block_size(len(items))
        data_offsets[name] = pos
        pos += _data_block_size(items)
    thumb_offset = pos if thumb else 0
    total = pos + len(thumb)

    buf = bytearray(total)
    buf[0:2] = b"II" if tiff.endian == "II" else b"MM"
    struct.pack_into(e + "H", buf, 2, 42)
    struct.pack_into(e + "I", buf, 4, offsets["ifd0"])

    # 指针与缩略图偏移（此时布局已固定；必须用目标字节序写）
    def _ptr(value: int) -> bytes:
        return struct.pack(e + "I", value)

    if TAG_EXIF_IFD in g0:
        g0[TAG_EXIF_IFD].raw = _ptr(offsets["exif"])
    if TAG_GPS_IFD in g0:
        g0[TAG_GPS_IFD].raw = _ptr(offsets["gps"])
    if TAG_INTEROP_IFD in ge:
        ge[TAG_INTEROP_IFD].raw = _ptr(offsets["interop"])
    if thumb:
        g1[TAG_THUMB_OFFSET].raw = _ptr(thumb_offset)
        g1[TAG_THUMB_LENGTH].raw = _ptr(len(thumb))

    next_map = {"ifd0": offsets["ifd1"] if lists["ifd1"] else 0}

    for name in order:
        items = lists[name]
        if not items:
            continue
        off = offsets[name]
        data_ptr = data_offsets[name]
        items.sort(key=lambda x: x.tag)
        struct.pack_into(e + "H", buf, off, len(items))
        for i, ent in enumerate(items):
            p = off + 2 + i * 12
            raw = ent.raw
            size = ent.byte_size
            struct.pack_into(e + "HHI", buf, p, ent.tag, ent.typ, ent.count)
            if size <= 4:
                buf[p + 8:p + 8 + size] = raw[:size]
            else:
                struct.pack_into(e + "I", buf, p + 8, data_ptr)
                buf[data_ptr:data_ptr + size] = raw[:size]
                data_ptr += size + (size & 1)
        struct.pack_into(e + "I", buf, off + 2 + 12 * len(items), next_map.get(name, 0))

    if thumb:
        buf[thumb_offset:thumb_offset + len(thumb)] = thumb
    return bytes(buf)


# ---------------------------------------------------------------- 解析

def _read_ifd(raw: bytes, off: int, endian: str, depth: int = 0) -> tuple[dict, int]:
    e = "<" if endian == "II" else ">"
    if depth > 4:
        raise ExifError("IFD 嵌套过深")
    if off <= 0 or off + 2 > len(raw):
        raise ExifError(f"IFD 偏移越界: {off}")
    n = struct.unpack_from(e + "H", raw, off)[0]
    if n > 4096:
        raise ExifError(f"IFD 条目过多: {n}")
    if off + 2 + 12 * n + 4 > len(raw):
        raise ExifError("IFD 数据截断")
    entries: dict[int, Entry] = {}
    for i in range(n):
        p = off + 2 + i * 12
        tag, typ, cnt = struct.unpack_from(e + "HHI", raw, p)
        size = TYPE_SIZE.get(typ)
        if size is None:
            size = 1
        total = size * cnt
        if total <= 4:
            val = raw[p + 8:p + 8 + total]
        else:
            voff = struct.unpack_from(e + "I", raw, p + 8)[0]
            if voff + total > len(raw):
                # 截断：保留能读到的部分
                val = raw[voff:]
            else:
                val = raw[voff:voff + total]
        entries[tag] = Entry(tag, typ, cnt, val)
    nxt = struct.unpack_from(e + "I", raw, off + 2 + 12 * n)[0]
    return entries, nxt


def parse_tiff(raw: bytes) -> Tiff:
    """解析裸 TIFF（不含 Exif\\0\\0 前缀）。"""
    if len(raw) < 8:
        raise ExifError("数据过短")
    if raw[0:2] == b"II":
        endian = "II"
    elif raw[0:2] == b"MM":
        endian = "MM"
    else:
        raise ExifError("不是 TIFF 头")
    e = "<" if endian == "II" else ">"
    magic = struct.unpack_from(e + "H", raw, 2)[0]
    if magic != 42:
        raise ExifError(f"TIFF 魔数错误: {magic}")
    ifd0_off = struct.unpack_from(e + "I", raw, 4)[0]

    t = Tiff(endian)
    t.raw = raw
    ifd0, nxt = _read_ifd(raw, ifd0_off, endian)
    t.ifd0 = ifd0

    ptr = ifd0.get(TAG_EXIF_IFD)
    if ptr is not None:
        exif_off = ptr.first_int(endian)
        if 0 < exif_off < len(raw):
            t.exif, _ = _read_ifd(raw, exif_off, endian, 1)
    gptr = ifd0.get(TAG_GPS_IFD)
    if gptr is not None:
        gps_off = gptr.first_int(endian)
        if 0 < gps_off < len(raw):
            t.gps, _ = _read_ifd(raw, gps_off, endian, 1)
    iptr = t.exif.get(TAG_INTEROP_IFD)
    if iptr is not None:
        i_off = iptr.first_int(endian)
        if 0 < i_off < len(raw):
            t.interop, _ = _read_ifd(raw, i_off, endian, 2)

    if nxt and 0 < nxt < len(raw):
        try:
            t.ifd1, _ = _read_ifd(raw, nxt, endian, 1)
        except ExifError:
            t.ifd1 = {}
        off_ent = t.ifd1.get(TAG_THUMB_OFFSET)
        len_ent = t.ifd1.get(TAG_THUMB_LENGTH)
        if off_ent is not None and len_ent is not None:
            toff = off_ent.first_int(endian)
            tlen = len_ent.first_int(endian)
            if 0 < toff < len(raw) and tlen > 0 and toff + tlen <= len(raw):
                t.thumb = raw[toff:toff + tlen]
            t.ifd1.pop(TAG_THUMB_OFFSET, None)
            t.ifd1.pop(TAG_THUMB_LENGTH, None)
    return t


def new_tiff(dt: datetime, offset: str | None = None, make: str | None = None,
             model: str | None = None) -> Tiff:
    t = Tiff("II")
    t.put("ifd0", entry_ascii(TAG_SOFTWARE, "PhotoMetaFix"))
    if make:
        t.put("ifd0", entry_ascii(TAG_MAKE, make))
    if model:
        t.put("ifd0", entry_ascii(TAG_MODEL, model))
    t.set_dates(dt, offset)
    return t


def to_bytes_or_none(t: Tiff) -> bytes | None:
    try:
        return t.to_bytes()
    except ExifError:
        return None
