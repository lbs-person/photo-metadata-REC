"""统一读取入口：把不同容器里的时间线索汇总成统一结构。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import exif as exifmod
from . import heif, iptc, jpeg, mp4, png, sniff, xmp

_MAX_IMAGE = 512 * 1024 * 1024
_MP4_HEAD = 96 * 1024 * 1024


@dataclass
class Probe:
    path: str
    kind: str
    size: int
    has_exif_block: bool = False
    tiff: object | None = None
    exif_dates: dict = field(default_factory=dict)
    extra_dates: list = field(default_factory=list)
    writable: bool = True
    error: str | None = None
    notes: list = field(default_factory=list)


def _read(path: str, limit: int) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(limit)


def probe(path: str) -> Probe:
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return Probe(path, sniff.KIND_OTHER, 0, error=str(exc), writable=False)

    kind = sniff.kind_of(path)
    p = Probe(path, kind, size)
    if kind == sniff.KIND_OTHER:
        p.error = "不支持的文件类型"
        p.writable = False
        return p

    limit = _MP4_HEAD if kind == sniff.KIND_MP4 else _MAX_IMAGE
    try:
        data = _read(path, limit)
    except OSError as exc:
        p.error = f"读取失败: {exc}"
        p.writable = False
        return p

    try:
        if kind == sniff.KIND_JPEG:
            _probe_jpeg(p, data)
        elif kind == sniff.KIND_HEIC:
            _probe_heic(p, data)
        elif kind == sniff.KIND_PNG:
            _probe_png(p, data)
        elif kind == sniff.KIND_TIFF:
            _probe_tiff(p, data)
        elif kind == sniff.KIND_MP4:
            _probe_mp4(p, data)
    except Exception as exc:  # 坏文件不应让整个扫描崩掉
        p.error = f"解析失败: {exc}"
        p.writable = False

    # XMP 边车
    side = os.path.splitext(path)[0] + ".xmp"
    if os.path.isfile(side):
        try:
            p.extra_dates.extend(xmp.parse_xmp_dates(_read(side, 4 * 1024 * 1024)))
            p.notes.append("发现同名 .xmp 边车文件")
        except OSError:
            pass
    return p


def _probe_jpeg(p: Probe, data: bytes):
    raw = jpeg.extract_exif(data)
    p.has_exif_block = raw is not None
    if raw:
        try:
            p.tiff = exifmod.parse_tiff(raw)
            p.exif_dates = p.tiff.date_info()
        except exifmod.ExifError as exc:
            p.notes.append(f"EXIF 损坏: {exc}")
    try:
        xmp_raw = jpeg.extract_xmp(data)
        if xmp_raw:
            p.extra_dates.extend(xmp.parse_xmp_dates(xmp_raw))
    except jpeg.JpegError:
        pass
    try:
        segs, _ = jpeg.split(data)
        for marker, payload in segs:
            if marker == 0xED and (payload.startswith(iptc.PHOTOSHOP_HEADER)
                                   or b"8BIM" in payload[:64]):
                p.extra_dates.extend(iptc.parse_iptc_dates(iptc.extract_iptc(payload)))
                break
    except jpeg.JpegError:
        pass


def _probe_heic(p: Probe, data: bytes):
    try:
        h = heif.Heif(data)
    except heif.HeifError as exc:
        p.has_exif_block = False
        p.notes.append(f"HEIC 结构异常: {exc}")
        return
    p.has_exif_block = h.exif_item is not None
    t = heif.read_tiff(data)
    if t is not None:
        p.tiff = t
        p.exif_dates = t.date_info()
    elif not p.has_exif_block:
        p.notes.append("HEIC 内没有 Exif 项（只能写文件时间）")
    else:
        p.notes.append("Exif 项存在但无法解析")


def _probe_png(p: Probe, data: bytes):
    t = png.read_tiff(data)
    p.has_exif_block = t is not None
    if t is not None:
        p.tiff = t
        p.exif_dates = t.date_info()


def _probe_tiff(p: Probe, data: bytes):
    try:
        t = exifmod.parse_tiff(data)
    except exifmod.ExifError as exc:
        p.notes.append(f"TIFF 解析失败: {exc}")
        return
    p.tiff = t
    p.has_exif_block = True
    p.exif_dates = t.date_info()


def _probe_mp4(p: Probe, data: bytes):
    p.writable = False
    p.extra_dates.extend(mp4.read_dates(data))
    if not p.extra_dates:
        p.notes.append("视频内没有可用的日期字段")


def write_dates(path: str, kind: str, tiff_bytes: bytes) -> None:
    """把新的 TIFF 写回文件（原子替换）。"""
    with open(path, "rb") as fh:
        data = fh.read()
    if kind == sniff.KIND_JPEG:
        new = jpeg.write_tiff(data, tiff_bytes)
    elif kind == sniff.KIND_HEIC:
        new = heif.write_tiff(data, tiff_bytes)
    elif kind == sniff.KIND_PNG:
        new = png.write_tiff(data, tiff_bytes)
    elif kind == sniff.KIND_TIFF:
        new = tiff_bytes
    else:
        raise ValueError(f"不支持写入的类型: {kind}")

    tmp = path + ".pmfix.tmp"
    with open(tmp, "wb") as fh:
        fh.write(new)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.replace(tmp, path)
    except OSError:
        # 目标被占用时退回直接覆盖
        try:
            with open(path, "wb") as fh:
                fh.write(new)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
