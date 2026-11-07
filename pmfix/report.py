"""导出 CSV / JSON 报告。"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime

from . import scanner

HEADERS = ["文件名", "相对路径", "类型", "大小(MB)", "原EXIF拍摄时间", "当前文件时间",
           "建议来源", "建议时间", "置信度", "状态", "已勾选", "处理结果", "备注"]


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def rows_to_records(rows: list[scanner.Row]) -> list[dict]:
    out = []
    for r in rows:
        try:
            mt = datetime.fromtimestamp(os.path.getmtime(r.path))
        except OSError:
            mt = None
        out.append({
            "文件名": r.name,
            "相对路径": r.rel,
            "类型": r.kind,
            "大小(MB)": round(r.size / 1048576, 2),
            "原EXIF拍摄时间": _fmt(r.exif_dt),
            "当前文件时间": _fmt(mt),
            "建议来源": r.source_label,
            "建议时间": _fmt(r.target_dt),
            "置信度": r.confidence,
            "状态": scanner.STATUS_TEXT.get(r.status, r.status),
            "已勾选": "是" if r.selected else "否",
            "处理结果": r.note,
            "备注": "; ".join(r.probe.notes) if getattr(r.probe, "notes", None) else "",
        })
    return out


def write_csv(path: str, rows: list[scanner.Row]) -> None:
    records = rows_to_records(rows)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADERS)
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)


def write_json(path: str, rows: list[scanner.Row]) -> None:
    data = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(rows),
        "files": rows_to_records(rows),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
