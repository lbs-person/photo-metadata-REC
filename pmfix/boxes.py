"""ISOBMFF（ISO 14496-12）盒子遍历，供 MP4 / HEIC 共用。"""

from __future__ import annotations

import struct


def iter_boxes(data: bytes, start: int, end: int):
    """产出 (type, payload_start, payload_end, box_start)。"""
    i = start
    while i + 8 <= end:
        size = struct.unpack_from(">I", data, i)[0]
        btype = data[i + 4:i + 8]
        header = 8
        if size == 1:
            if i + 16 > end:
                return
            size = struct.unpack_from(">Q", data, i + 8)[0]
            header = 16
        elif size == 0:
            size = end - i
        if size < header or i + size > end:
            return
        yield btype, i + header, i + size, i
        i += size


def find_box(data: bytes, start: int, end: int, btype: bytes):
    for t, ps, pe, bs in iter_boxes(data, start, end):
        if t == btype:
            return ps, pe
    return None
