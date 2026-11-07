"""HEIC / HEIF（ISOBMFF）容器：读写 Exif item。

iPhone 拍的 HEIC 几乎都带一个 `Exif` 元数据 item。iCloud 网页端下载后
该 item 通常还在，但拍摄时间字段可能被清空 —— 这正是本模块要改的地方。

写入策略（按风险从低到高）：
  1. 新数据 <= 原 extent 长度  ：原地覆盖，多余补 0，只改 length 字段。
  2. 否则                     ：把新 Exif 追加到文件末尾，改写 iloc 里的
                                offset/length —— 文件其余字节完全不动。
两种情况都不移动任何已有数据，因此不会破坏 mdat 里的图像数据。
"""

from __future__ import annotations

import struct

from . import exif as exifmod
from .boxes import find_box, iter_boxes

HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis",
               b"mif1", b"msf1", b"heif", b"avif", b"avis")


class HeifError(Exception):
    pass


class _Extent:
    __slots__ = ("off_pos", "off_size", "len_pos", "len_size", "offset", "length")

    def __init__(self, off_pos=0, off_size=0, len_pos=0, len_size=0, offset=0, length=0):
        self.off_pos = off_pos
        self.off_size = off_size
        self.len_pos = len_pos
        self.len_size = len_size
        self.offset = offset
        self.length = length


class _Item:
    __slots__ = ("item_id", "item_type", "name", "cm", "cm_pos",
                 "base_pos", "base_size", "base_offset",
                 "count_pos", "extents")

    def __init__(self):
        self.item_id = 0
        self.item_type = b""
        self.name = ""
        self.cm = 0
        self.cm_pos = None
        self.base_pos = 0
        self.base_size = 0
        self.base_offset = 0
        self.count_pos = 0
        self.extents: list[_Extent] = []


def _read_uint(data: bytes, pos: int, size: int) -> int:
    if size == 0:
        return 0
    if pos + size > len(data):
        raise HeifError("iloc 越界")
    return int.from_bytes(data[pos:pos + size], "big")


class Heif:
    def __init__(self, data: bytes):
        self.data = data
        self.meta: tuple[int, int] | None = None
        self.iloc: tuple[int, int] | None = None
        self.iinf: tuple[int, int] | None = None
        self.idat: tuple[int, int] | None = None
        self.ver = 0
        self.off_size = 0
        self.len_size = 0
        self.base_size = 0
        self.index_size = 0
        self.items: dict[int, _Item] = {}
        self.infe: dict[int, bytes] = {}
        self._parse()

    # -- 解析
    def _parse(self):
        data = self.data
        self.meta = find_box(data, 0, len(data), b"meta")
        if self.meta is None:
            raise HeifError("缺少 meta 盒")
        ps, pe = self.meta
        p = ps + 4  # FullBox version/flags
        self.iinf = find_box(data, p, pe, b"iinf")
        self.iloc = find_box(data, p, pe, b"iloc")
        self.idat = find_box(data, p, pe, b"idat")
        if self.iloc is None:
            raise HeifError("缺少 iloc 盒")
        if self.iinf is not None:
            self._parse_iinf()
        self._parse_iloc()

    def _parse_iinf(self):
        data = self.data
        ps, pe = self.iinf
        ver = data[ps]
        p = ps + 4
        count = struct.unpack_from(">H", data, p)[0] if ver == 0 else struct.unpack_from(">I", data, p)[0]
        p += 2 if ver == 0 else 4
        for _ in range(count):
            if p + 8 > pe:
                break
            size = struct.unpack_from(">I", data, p)[0]
            btype = data[p + 4:p + 8]
            if size < 8 or p + size > pe:
                break
            if btype == b"infe":
                q = p + 8
                iver = data[q]
                q += 4
                if iver in (2, 3):
                    item_id = struct.unpack_from(">H", data, q)[0] if iver == 2 else struct.unpack_from(">I", data, q)[0]
                    q += 2 if iver == 2 else 4
                    q += 2  # protection index
                    itype = data[q:q + 4]
                    self.infe[item_id] = itype
                else:
                    item_id = struct.unpack_from(">H", data, q)[0]
                    self.infe[item_id] = b""
            p += size

    def _parse_iloc(self):
        data = self.data
        ps, pe = self.iloc
        ver = data[ps]
        self.ver = ver
        p = ps + 4
        sizes = data[p]
        self.off_size = sizes >> 4
        self.len_size = sizes & 0x0F
        sizes2 = data[p + 1]
        self.base_size = sizes2 >> 4
        self.index_size = sizes2 & 0x0F if ver in (1, 2) else 0
        p += 2
        if ver < 2:
            count = struct.unpack_from(">H", data, p)[0]
            p += 2
        else:
            count = struct.unpack_from(">I", data, p)[0]
            p += 4
        for _ in range(count):
            item = _Item()
            if ver < 2:
                item.item_id = struct.unpack_from(">H", data, p)[0]
                p += 2
            else:
                item.item_id = struct.unpack_from(">I", data, p)[0]
                p += 4
            if ver in (1, 2):
                item.cm_pos = p
                item.cm = data[p + 1] & 0x0F
                p += 2
            p += 2  # data_reference_index
            item.base_pos = p
            item.base_size = self.base_size
            item.base_offset = _read_uint(data, p, self.base_size)
            p += self.base_size
            item.count_pos = p
            ec = struct.unpack_from(">H", data, p)[0]
            p += 2
            for _k in range(ec):
                if self.index_size:
                    p += self.index_size
                ext = _Extent()
                ext.off_size = self.off_size
                ext.len_size = self.len_size
                ext.off_pos = p
                ext.offset = _read_uint(data, p, self.off_size)
                p += self.off_size
                ext.len_pos = p
                ext.length = _read_uint(data, p, self.len_size)
                p += self.len_size
                item.extents.append(ext)
            self.items[item.item_id] = item

    # -- 查询
    @property
    def exif_item(self) -> _Item | None:
        for item_id, itype in self.infe.items():
            if itype == b"Exif":
                return self.items.get(item_id)
        return None

    def data_start(self, item: _Item, ext: _Extent) -> int:
        if item.cm == 0:
            return item.base_offset + ext.offset
        if item.cm == 1:
            if self.idat is None:
                raise HeifError("construction_method=1 但没有 idat")
            return self.idat[0] + item.base_offset + ext.offset
        raise HeifError(f"不支持的 construction_method={item.cm}")

    def read_item(self, item: _Item) -> bytes:
        chunks = []
        for ext in item.extents:
            if ext.length <= 0:
                continue
            start = self.data_start(item, ext)
            if start < 0 or start + ext.length > len(self.data):
                raise HeifError("Exif 数据越界")
            chunks.append(self.data[start:start + ext.length])
        return b"".join(chunks)


def _looks_heif(data: bytes) -> bool:
    if len(data) < 12 or data[4:8] != b"ftyp":
        return False
    return data[8:12] in HEIC_BRANDS


def is_heif(data: bytes) -> bool:
    try:
        return _looks_heif(data)
    except Exception:  # pragma: no cover
        return False


def parse(data: bytes) -> Heif:
    return Heif(data)


def read_tiff(data: bytes):
    """返回解析后的 Tiff，或 None。"""
    try:
        h = Heif(data)
    except HeifError:
        return None
    item = h.exif_item
    if item is None:
        return None
    try:
        payload = h.read_item(item)
    except HeifError:
        return None
    if not payload:
        return None
    if payload.startswith(exifmod.EXIF_HEADER):
        raw = payload[len(exifmod.EXIF_HEADER):]
    elif len(payload) >= 4:
        skip = struct.unpack_from(">I", payload, 0)[0]
        if skip <= len(payload) - 4:
            raw = payload[4 + skip:]
        else:
            raw = payload[4:]
    else:
        return None
    try:
        return exifmod.parse_tiff(raw)
    except exifmod.ExifError:
        return None


def _set_uint(buf: bytearray, pos: int, size: int, value: int) -> None:
    if size == 0:
        if value != 0:
            raise HeifError("iloc 字段宽度为 0，无法写入偏移")
        return
    if value < 0 or value >= (1 << (size * 8)):
        raise HeifError(f"数值 {value} 超出 {size} 字节范围")
    buf[pos:pos + size] = value.to_bytes(size, "big")


def write_tiff(data: bytes, tiff_bytes: bytes) -> bytes:
    """把新的 TIFF 写入 HEIC 的 Exif item。"""
    h = Heif(data)
    item = h.exif_item
    if item is None:
        raise HeifError("该 HEIC 内没有 Exif 元数据项，无法写入")
    if len(item.extents) != 1:
        raise HeifError("Exif 数据分片存储（多 extent），暂不支持改写")
    ext = item.extents[0]
    payload = b"\x00\x00\x00\x00" + tiff_bytes
    buf = bytearray(data)

    old_start = h.data_start(item, ext)
    if ext.length and len(payload) <= ext.length and old_start + ext.length <= len(buf):
        # 1) 原地覆盖
        buf[old_start:old_start + len(payload)] = payload
        if len(payload) < ext.length:
            buf[old_start + len(payload):old_start + ext.length] = b"\x00" * (ext.length - len(payload))
        _set_uint(buf, ext.len_pos, ext.len_size, len(payload))
        return bytes(buf)

    # 2) 追加到文件末尾并重新指向
    new_off = len(buf)
    _set_uint(buf, ext.off_pos, ext.off_size, new_off)
    _set_uint(buf, ext.len_pos, ext.len_size, len(payload))
    if item.cm_pos is not None:
        buf[item.cm_pos + 1] = buf[item.cm_pos + 1] & 0xF0  # construction_method = 0
    _set_uint(buf, item.base_pos, item.base_size, 0)
    buf += payload
    return bytes(buf)


def describe(data: bytes) -> dict:
    try:
        h = Heif(data)
    except HeifError as exc:
        return {"ok": False, "error": str(exc)}
    item = h.exif_item
    return {
        "ok": True,
        "iloc_version": h.ver,
        "off_size": h.off_size,
        "len_size": h.len_size,
        "base_size": h.base_size,
        "items": len(h.items),
        "exif_item": None if item is None else {
            "id": item.item_id,
            "cm": item.cm,
            "extents": len(item.extents),
            "length": item.extents[0].length if item.extents else 0,
        },
    }
