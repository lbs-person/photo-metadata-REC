"""JPEG 容器：拆分 APPn 段、替换 / 插入 EXIF 与 XMP。"""

from __future__ import annotations

import struct

from . import exif as exifmod

SOI = 0xD8
EOI = 0xD9
SOS = 0xDA
APP0 = 0xE0
APP1 = 0xE1
APP2 = 0xE2
APP13 = 0xED


class JpegError(Exception):
    pass


def split(data: bytes):
    """返回 (segments, tail)。

    segments: [(marker:int, payload:bytes), ...]  —— SOS 之前的所有段
    tail:     从 SOS 标记开始直到文件末尾的原始字节
    """
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        raise JpegError("不是 JPEG（缺少 SOI）")
    segs = []
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            raise JpegError(f"段结构损坏，偏移 {i}")
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            raise JpegError("文件截断")
        marker = data[i]
        i += 1
        if marker == SOS or marker == EOI:
            return segs, data[i - 2:]
        if marker == SOI or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            segs.append((marker, b""))
            continue
        if i + 2 > n:
            raise JpegError("段长度截断")
        length = struct.unpack_from(">H", data, i)[0]
        if length < 2 or i + length > n:
            raise JpegError("段长度非法")
        segs.append((marker, data[i + 2:i + length]))
        i += length
    raise JpegError("未找到 SOS")


def join(segments, tail: bytes) -> bytes:
    out = bytearray(b"\xff\xd8")
    for marker, payload in segments:
        out += bytes((0xFF, marker))
        if marker in (SOI, EOI, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        out += struct.pack(">H", len(payload) + 2)
        out += payload
    out += tail
    return bytes(out)


def _is_exif_app1(payload: bytes) -> bool:
    return payload.startswith(exifmod.EXIF_HEADER)


def _is_xmp_app1(payload: bytes) -> bool:
    return payload.startswith(exifmod.XMP_HEADER)


def extract_exif(data: bytes) -> bytes | None:
    """取出 APP1 里的裸 TIFF 字节。"""
    segs, _ = split(data)
    for marker, payload in segs:
        if marker == APP1 and _is_exif_app1(payload):
            return payload[len(exifmod.EXIF_HEADER):]
    return None


def read_tiff(data: bytes):
    raw = extract_exif(data)
    if raw is None:
        return None
    try:
        return exifmod.parse_tiff(raw)
    except exifmod.ExifError:
        return None


def write_tiff(data: bytes, tiff_bytes: bytes, keep_xmp: bool = True) -> bytes:
    """把新的 TIFF 写进 JPEG，返回完整新文件字节。"""
    segs, tail = split(data)
    app1 = exifmod.EXIF_HEADER + tiff_bytes
    if len(app1) + 2 > 0xFFFF:
        raise JpegError("EXIF 过大，无法写入单个 APP1 段")

    out = []
    inserted = False
    for marker, payload in segs:
        if marker == APP1 and _is_exif_app1(payload):
            continue  # 丢弃旧的 EXIF
        if marker == APP1 and _is_xmp_app1(payload) and not keep_xmp:
            continue
        if not inserted and marker == APP1 and _is_xmp_app1(payload):
            out.append((APP1, app1))
            inserted = True
        out.append((marker, payload))
    if not inserted:
        # 紧跟 SOI 插入（在 APP0/JFIF 之前或之后都可，这里放在最前）
        out.insert(0, (APP1, app1))
    return join(out, tail)


def has_metadata(data: bytes) -> dict:
    """快速统计：是否有 EXIF / XMP。"""
    segs, _ = split(data)
    info = {"exif": False, "xmp": False, "iptc": False, "icc": False}
    for marker, payload in segs:
        if marker == APP1 and _is_exif_app1(payload):
            info["exif"] = True
        elif marker == APP1 and _is_xmp_app1(payload):
            info["xmp"] = True
        elif marker == APP13 and (b"Photoshop 3.0" in payload[:16] or b"Adobe_Photoshop2.5" in payload[:20]):
            info["iptc"] = True
        elif marker == APP2 and payload.startswith(b"ICC_PROFILE"):
            info["icc"] = True
    return info


def extract_xmp(data: bytes) -> bytes | None:
    segs, _ = split(data)
    for marker, payload in segs:
        if marker == APP1 and _is_xmp_app1(payload):
            return payload[len(exifmod.XMP_HEADER):]
    return None


def strip_metadata(data: bytes) -> bytes:
    """测试用：删掉所有 APPn 段（模拟被剥离元数据的照片）。"""
    segs, tail = split(data)
    keep = [(m, p) for m, p in segs if not (0xE0 <= m <= 0xEF)]
    return join(keep, tail)
