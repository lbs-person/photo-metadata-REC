"""PNG 容器：读写 eXIf 块（PNG 1.5+ 内嵌 TIFF）。"""

from __future__ import annotations

import struct
import zlib

from . import exif as exifmod

PNG_SIG = b"\x89PNG\r\n\x1a\n"


class PngError(Exception):
    pass


def iter_chunks(data: bytes):
    if not data.startswith(PNG_SIG):
        raise PngError("不是 PNG")
    i = 8
    n = len(data)
    while i + 8 <= n:
        length = struct.unpack_from(">I", data, i)[0]
        ctype = data[i + 4:i + 8]
        end = i + 8 + length
        if end + 4 > n:
            raise PngError("PNG 块截断")
        yield i, ctype, data[i + 8:end], data[end:end + 4]
        i = end + 4
        if ctype == b"IEND":
            break


def chunks(data: bytes):
    return [(i, t, d) for i, t, d, _c in iter_chunks(data)]


def read_tiff(data: bytes):
    for _i, ctype, payload in chunks(data):
        if ctype == b"eXIf":
            try:
                return exifmod.parse_tiff(payload)
            except exifmod.ExifError:
                return None
    return None


def _make_chunk(ctype: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def write_tiff(data: bytes, tiff_bytes: bytes) -> bytes:
    """替换 / 插入 eXIf 块，其余块原样保留。"""
    out = bytearray(PNG_SIG)
    inserted = False
    for _i, ctype, payload, crc in iter_chunks(data):
        if ctype == b"eXIf":
            continue
        out += struct.pack(">I", len(payload)) + ctype + payload + crc
        if ctype == b"IHDR" and not inserted:
            out += _make_chunk(b"eXIf", tiff_bytes)
            inserted = True
    if not inserted:
        raise PngError("PNG 缺少 IHDR")
    return bytes(out)


def has_metadata(data: bytes) -> dict:
    info = {"exif": False, "xmp": False, "text": False}
    for _i, ctype, payload in chunks(data):
        if ctype == b"eXIf":
            info["exif"] = True
        elif ctype in (b"iTXt", b"tEXt", b"zTXt"):
            info["text"] = True
            if ctype == b"iTXt" and payload.startswith(b"XML:com.adobe.xmp"):
                info["xmp"] = True
    return info
