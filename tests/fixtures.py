"""构造测试用的最小合法媒体文件（不依赖任何第三方库）。"""

from __future__ import annotations

import base64
import struct
import zlib

from pmfix import exif as X

# 64x48 的真 JPEG（由 System.Drawing 生成），保证能被真正的解码器打开
_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAwAEADASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm"
    "p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA"
    "AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx"
    "BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK"
    "U1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3"
    "uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDQooor"
    "648MKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigD/2Q=="
)


def jpeg_base() -> bytes:
    return base64.b64decode(_JPEG_B64)


def jpeg_exif(tiff_bytes: bytes | None) -> bytes:
    """在真 JPEG 的 SOI 之后插入（或替换）APP1 EXIF 段。"""
    data = jpeg_base()
    if not tiff_bytes:
        return data
    payload = X.EXIF_HEADER + tiff_bytes
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return data[:2] + seg + data[2:]


# ---------------------------------------------------------------- TIFF

def tiff_with_everything() -> bytes:
    """带 GPS、缩略图、MakerNote 的完整 EXIF，用来验证「修改后不丢东西」。"""
    t = X.Tiff("II")
    t.put("ifd0", X.entry_ascii(X.TAG_MAKE, "Apple"))
    t.put("ifd0", X.entry_ascii(X.TAG_MODEL, "iPhone 14 Pro"))
    t.put("ifd0", X.entry_ascii(X.TAG_SOFTWARE, "17.4.1"))
    t.put("ifd0", X.entry_short(X.TAG_ORIENTATION, 6))
    t.put("ifd0", X.Entry(X.TAG_XRES, X.T_RATIONAL, 1, X.enc_rational((72, 1))))
    t.put("ifd0", X.Entry(X.TAG_YRES, X.T_RATIONAL, 1, X.enc_rational((72, 1))))
    t.put("ifd0", X.entry_short(X.TAG_RESUNIT, 2))
    t.put("exif", X.entry_ascii(X.TAG_DATETIME_ORIGINAL, "2021:03:04 05:06:07"))
    t.put("exif", X.entry_ascii(X.TAG_DATETIME_DIGITIZED, "2021:03:04 05:06:07"))
    t.put("exif", X.entry_ascii(X.TAG_OFFSET_TIME_ORIGINAL, "+08:00"))
    t.put("exif", X.entry_short(X.TAG_ISO, 200))
    t.put("exif", X.Entry(X.TAG_FNUMBER, X.T_RATIONAL, 1, X.enc_rational((178, 100))))
    t.put("exif", X.Entry(X.TAG_MAKER_NOTE, X.T_UNDEFINED, 16,
                          b"Apple iOS\x00" + b"\x00" * 6))
    t.put("gps", X.Entry(X.TAG_GPS_VERSION, X.T_BYTE, 4, b"\x02\x03\x00\x00"))
    t.put("gps", X.entry_ascii(X.TAG_GPS_LAT_REF, "N"))
    t.put("gps", X.Entry(X.TAG_GPS_LAT, X.T_RATIONAL, 3,
                         X.enc_rational((31, 1), (14, 1), (1234, 100))))
    t.put("gps", X.entry_ascii(X.TAG_GPS_LON_REF, "E"))
    t.put("gps", X.Entry(X.TAG_GPS_LON, X.T_RATIONAL, 3,
                         X.enc_rational((121, 1), (28, 1), (5678, 100))))
    t.thumb = b"\xff\xd8THUMBNAILDATA\xff\xd9"
    t.put("ifd1", X.entry_short(X.TAG_ORIENTATION, 6))
    return t.to_bytes()


def tiff_no_dates() -> bytes:
    t = X.Tiff("II")
    t.put("ifd0", X.entry_ascii(X.TAG_MAKE, "Apple"))
    t.put("ifd0", X.entry_ascii(X.TAG_MODEL, "iPhone 14 Pro"))
    t.put("exif", X.entry_short(X.TAG_ISO, 100))
    return t.to_bytes()


# ---------------------------------------------------------------- PNG

def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def png_plain(width: int = 2, height: int = 2) -> bytes:
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(raw))
            + _png_chunk(b"IEND", b""))


# ---------------------------------------------------------------- HEIC

def _box(btype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + btype + payload


def _full_box(btype: bytes, ver: int, flags: int, payload: bytes) -> bytes:
    return _box(btype, bytes([ver]) + flags.to_bytes(3, "big") + payload)


def _infe(item_id: int, item_type: bytes, name: str = "") -> bytes:
    payload = struct.pack(">HH", item_id, 0) + item_type + name.encode() + b"\x00"
    return _full_box(b"infe", 2, 0, payload)


def make_heic(tiff_bytes: bytes | None = None,
              image_payload: bytes = b"IMAGEDATA" * 64) -> bytes:
    """构造一个 iloc v0（offset_size=4）的 HEIC：ftyp + meta + mdat。"""
    ftyp = _box(b"ftyp", b"heic" + struct.pack(">I", 0) + b"mif1" + b"heic")
    hdlr = _full_box(b"hdlr", 0, 0,
                     struct.pack(">I", 0) + b"pict" + b"\x00" * 12 + b"\x00")
    pitm = _full_box(b"pitm", 0, 0, struct.pack(">H", 1))

    exif_payload = (b"\x00\x00\x00\x00" + tiff_bytes) if tiff_bytes else b""
    n_items = 2 if exif_payload else 1

    infe = _infe(1, b"hvc1", "image")
    if exif_payload:
        infe += _infe(2, b"Exif")
    iinf = _full_box(b"iinf", 0, 0, struct.pack(">H", n_items) + infe)

    iprp = _box(b"iprp",
                _box(b"ipco", _box(b"hvcC", b"\x01" * 8))
                + _full_box(b"ipma", 0, 0, struct.pack(">IH", 1, 1) + b"\x81\x00"))

    def build_iloc(off_img, len_img, off_exif, len_exif):
        p = bytes([0x44, 0x00])                      # 4/4/0/0
        p += struct.pack(">H", n_items)
        p += struct.pack(">HH", 1, 0)                # item_id, data_reference_index
        p += struct.pack(">H", 1) + struct.pack(">II", off_img, len_img)
        if exif_payload:
            p += struct.pack(">HH", 2, 0)
            p += struct.pack(">H", 1) + struct.pack(">II", off_exif, len_exif)
        return _full_box(b"iloc", 0, 0, p)

    dummy = build_iloc(0, len(image_payload), 0, len(exif_payload))
    meta_len = len(_box(b"meta", b"\x00\x00\x00\x00" + hdlr + pitm + iinf
                        + dummy + iprp))
    payload_start = len(ftyp) + meta_len + 8

    off_exif = payload_start + len(image_payload)
    iloc = build_iloc(payload_start, len(image_payload), off_exif, len(exif_payload))
    meta = _box(b"meta", b"\x00\x00\x00\x00" + hdlr + pitm + iinf + iloc + iprp)
    mdat = _box(b"mdat", image_payload + exif_payload)
    return ftyp + meta + mdat


# ---------------------------------------------------------------- MP4

def make_mov(day: str | None = "2021-03-04T05:06:07+0800") -> bytes:
    ftyp = _box(b"ftyp", b"qt  " + struct.pack(">I", 0) + b"qt  ")
    matrix = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    mvhd_payload = (struct.pack(">IIII", 0, 0, 600, 0)
                    + struct.pack(">I", 0x00010000)
                    + struct.pack(">H", 0x0100) + b"\x00\x00" + b"\x00" * 8
                    + matrix + b"\x00" * 24 + struct.pack(">I", 2))
    mvhd = _full_box(b"mvhd", 0, 0, mvhd_payload)

    udta = b""
    if day:
        data_box = _box(b"data", struct.pack(">II", 1, 0) + day.encode())
        udta = _box(b"udta",
                    _full_box(b"meta", 0, 0,
                              _full_box(b"hdlr", 0, 0,
                                        struct.pack(">I", 0) + b"mdir" + b"appl" + b"\x00" * 9)
                              + _box(b"ilst", _box(b"\xa9day", data_box))))
    moov = _box(b"moov", mvhd + udta)
    return ftyp + moov + _box(b"mdat", b"\x00" * 64)


# ---------------------------------------------------------------- iCloud 场景

def build_icloud_folder(root: str, download_time) -> dict:
    """模拟「iCloud 网页端下载 ZIP -> 解压」后的文件夹。"""
    import os

    os.makedirs(root, exist_ok=True)
    out = {}

    p = os.path.join(root, "2019-07-04 12.34.56.jpg")
    open(p, "wb").write(jpeg_exif(None))
    out["filename_jpg"] = p

    p = os.path.join(root, "IMG_0001.JPG")
    open(p, "wb").write(jpeg_exif(None))
    out["no_clue"] = p

    p = os.path.join(root, "IMG_2222.HEIC")
    open(p, "wb").write(make_heic(tiff_no_dates()))
    out["heic"] = p

    p = os.path.join(root, "IMG_3333.HEIC")
    open(p, "wb").write(make_heic(tiff_with_everything()))
    out["live_heic"] = p

    p = os.path.join(root, "IMG_3333.MOV")
    open(p, "wb").write(make_mov(None))
    out["live_mov"] = p

    p = os.path.join(root, "Screenshot_2020-01-02-03-04-05.png")
    open(p, "wb").write(png_plain())
    out["png"] = p

    ts = download_time.timestamp()
    for path in out.values():
        os.utime(path, (ts, ts))
    return out
