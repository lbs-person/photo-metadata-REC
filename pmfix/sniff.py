"""格式识别（扩展名 + 魔数，两者不一致时以魔数为准）。"""

from __future__ import annotations

import os

KIND_JPEG = "jpeg"
KIND_HEIC = "heic"
KIND_PNG = "png"
KIND_TIFF = "tiff"
KIND_MP4 = "mp4"
KIND_OTHER = "other"

IMAGE_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif", ".heic", ".heif", ".hif",
              ".png", ".tif", ".tiff", ".avif"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".qt"}
SIDECAR_EXTS = {".aae", ".xmp", ".json", ".plist", ".db", ".thm"}

_MP4_BRANDS = {b"isom", b"iso2", b"mp41", b"mp42", b"M4V ", b"M4A ",
               b"qt  ", b"3gp4", b"3gp5", b"avc1", b"dash", b"MSNV"}


def sniff(head: bytes, ext: str = "") -> str:
    ext = ext.lower()
    if head.startswith(b"\xff\xd8\xff"):
        return KIND_JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return KIND_PNG
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return KIND_TIFF
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis",
                     b"mif1", b"msf1", b"heif", b"avif", b"avis"):
            return KIND_HEIC
        if brand in _MP4_BRANDS:
            return KIND_MP4
        return KIND_MP4
    # 扩展名兜底
    if ext in (".heic", ".heif", ".hif"):
        return KIND_HEIC
    if ext in VIDEO_EXTS:
        return KIND_MP4
    if ext in (".tif", ".tiff"):
        return KIND_TIFF
    return KIND_OTHER


def kind_of(path: str | os.PathLike) -> str:
    ext = os.path.splitext(str(path))[1].lower()
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
    except OSError:
        return KIND_OTHER
    return sniff(head, ext)


def is_media(path: str | os.PathLike) -> bool:
    ext = os.path.splitext(str(path))[1].lower()
    return ext in IMAGE_EXTS or ext in VIDEO_EXTS
