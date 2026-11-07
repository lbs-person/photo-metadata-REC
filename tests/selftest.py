"""PhotoMetaFix 自测（无需 pytest，直接 python tests/selftest.py）。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fixtures  # noqa: E402
from pmfix import dates, exif as X, heif, jpeg, mp4, png, reader, repair, scanner  # noqa: E402

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  [FAIL] {name} {detail}")


def section(title: str) -> None:
    print(f"\n== {title} ==")


# ---------------------------------------------------------------- 1. TIFF/EXIF

def test_tiff():
    section("EXIF/TIFF 引擎")
    blob = fixtures.tiff_with_everything()
    t = X.parse_tiff(blob)
    d = t.date_info()
    check("解析 DateTimeOriginal", d["datetime_original"] == datetime(2021, 3, 4, 5, 6, 7),
          str(d["datetime_original"]))
    check("解析 OffsetTimeOriginal", d["offset_original"] == "+08:00", str(d["offset_original"]))
    check("解析 GPS 纬度", len(t.gps) >= 4, str(list(t.gps)))
    check("解析缩略图", len(t.thumb) > 0, str(len(t.thumb)))
    check("序列化幂等", X.parse_tiff(blob).to_bytes() == blob)

    # 改日期后，其它信息必须还在
    t.set_dates(datetime(2022, 1, 2, 3, 4, 5), "+09:00")
    blob2 = t.to_bytes()
    t2 = X.parse_tiff(blob2)
    d2 = t2.date_info()
    check("改写日期", d2["datetime_original"] == datetime(2022, 1, 2, 3, 4, 5), str(d2["datetime_original"]))
    check("改写后保留厂商信息", d2["make"] == "Apple" and d2["model"] == "iPhone 14 Pro")
    check("改写后保留 GPS", len(t2.gps) >= 4)
    check("改写后保留缩略图", t2.thumb == t.thumb)
    check("改写后保留 MakerNote",
          t2.exif[X.TAG_MAKER_NOTE].raw == t.exif[X.TAG_MAKER_NOTE].raw)

    # 大端
    tb = X.Tiff("MM")
    tb.put("ifd0", X.entry_ascii(X.TAG_MODEL, "Test"))
    tb.set_dates(datetime(2020, 6, 1, 0, 0, 0))
    tb2 = X.parse_tiff(tb.to_bytes())
    check("大端字节序", tb2.date_info()["datetime_original"] == datetime(2020, 6, 1))

    # 损坏输入
    try:
        X.parse_tiff(b"garbage!")
        check("损坏输入抛异常", False)
    except X.ExifError:
        check("损坏输入抛异常", True)


# ---------------------------------------------------------------- 2. JPEG

def test_jpeg():
    section("JPEG")
    base = fixtures.jpeg_base()
    check("原始 JPEG 无 EXIF", jpeg.extract_exif(base) is None)

    tiff = X.new_tiff(datetime(2018, 8, 8, 8, 8, 8))
    out = jpeg.write_tiff(base, tiff.to_bytes())
    t = jpeg.read_tiff(out)
    check("写入后可读回", t is not None and t.date_info()["datetime_original"] == datetime(2018, 8, 8, 8, 8, 8))

    segs, tail = jpeg.split(out)
    check("EXIF 位于第一个 APP1", segs[0][0] == 0xE1)
    check("图像数据原样保留", tail == jpeg.split(base)[1])

    # 重复写入应替换而不是叠加
    t2 = X.new_tiff(datetime(2019, 9, 9, 9, 9, 9))
    out2 = jpeg.write_tiff(out, t2.to_bytes())
    n_app1 = sum(1 for m, _p in jpeg.split(out2)[0] if m == 0xE1)
    check("重复写入只有一个 APP1", n_app1 == 1, f"实际 {n_app1}")
    check("重复写入值正确",
          jpeg.read_tiff(out2).date_info()["datetime_original"] == datetime(2019, 9, 9, 9, 9, 9))


# ---------------------------------------------------------------- 3. HEIC

def test_heic():
    section("HEIC")
    tiff = fixtures.tiff_no_dates()
    data = fixtures.make_heic(tiff)
    d = heif.describe(data)
    check("结构可解析", d.get("ok") and d["exif_item"] is not None, str(d))

    t = heif.read_tiff(data)
    check("读回 Exif", t is not None and t.date_info()["datetime_original"] is None)

    tiny = heif.read_tiff(data)
    tiny.set_dates(datetime(2017, 5, 6, 7, 8, 9))
    new = heif.write_tiff(data, tiny.to_bytes())
    check("追加写入后文件变长", len(new) > len(data), f"{len(data)} -> {len(new)}")
    t2 = heif.read_tiff(new)
    check("追加写入可读回",
          t2 is not None and t2.date_info()["datetime_original"] == datetime(2017, 5, 6, 7, 8, 9))
    check("原图像数据未移动", new[:len(data) - 0][:64] == data[:64])

    # 第二次写入长度不变 -> 走原地覆盖
    t2.set_dates(datetime(2016, 4, 3, 2, 1, 0))
    new2 = heif.write_tiff(new, t2.to_bytes())
    check("原地覆盖不改变文件长度", len(new2) == len(new), f"{len(new)} -> {len(new2)}")
    t3 = heif.read_tiff(new2)
    check("原地覆盖可读回",
          t3 is not None and t3.date_info()["datetime_original"] == datetime(2016, 4, 3, 2, 1, 0))

    # 完整 EXIF 的 HEIC：改写后 GPS 仍在
    data2 = fixtures.make_heic(fixtures.tiff_with_everything())
    t4 = heif.read_tiff(data2)
    t4.set_dates(datetime(2001, 2, 3, 4, 5, 6))
    new3 = heif.write_tiff(data2, t4.to_bytes())
    t5 = heif.read_tiff(new3)
    check("HEIC 改写保留 GPS", t5 is not None and len(t5.gps) >= 4)
    check("HEIC 改写保留缩略图", t5 is not None and t5.thumb == t4.thumb)

    # 没有 Exif item
    data3 = fixtures.make_heic(None)
    try:
        heif.write_tiff(data3, X.new_tiff(datetime(2020, 1, 1)).to_bytes())
        check("无 Exif item 时报错", False)
    except heif.HeifError:
        check("无 Exif item 时报错", True)


# ---------------------------------------------------------------- 4. PNG / MP4

def test_png_mp4():
    section("PNG / MP4")
    data = fixtures.png_plain()
    check("PNG 原始无 eXIf", png.read_tiff(data) is None)
    new = png.write_tiff(data, X.new_tiff(datetime(2015, 1, 1, 12, 0, 0)).to_bytes())
    t = png.read_tiff(new)
    check("PNG 写入可读回", t is not None and t.date_info()["datetime_original"] == datetime(2015, 1, 1, 12, 0, 0))
    check("PNG IEND 仍在", new.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"))

    mov = fixtures.make_mov("2021-03-04T05:06:07+0800")
    found = mp4.read_dates(mov)
    srcs = {f["source"] for f in found}
    check("MP4 读到 ©day", "mp4:©day" in srcs, str(srcs))
    day = [f for f in found if f["source"] == "mp4:©day"]
    check("MP4 日期值正确",
          bool(day) and day[0]["dt_local"] == datetime(2021, 3, 4, 5, 6, 7),
          str(day))
    check("MP4 偏移正确", bool(day) and day[0]["offset"] == "+08:00", str(day))

    mov2 = fixtures.make_mov(None)
    check("无 ©day 的 MOV 不伪造日期", mp4.read_dates(mov2) == [], str(mp4.read_dates(mov2)))


# ---------------------------------------------------------------- 5. 文件名

def test_filenames():
    section("文件名推断")
    cases = [
        ("2019-07-04 12.34.56.jpg", datetime(2019, 7, 4, 12, 34, 56)),
        ("IMG_20230704_123456.jpg", datetime(2023, 7, 4, 12, 34, 56)),
        ("PXL_20210715_101112345.jpg", datetime(2021, 7, 15, 10, 11, 12)),
        ("Screenshot_2020-01-02-03-04-05.png", datetime(2020, 1, 2, 3, 4, 5)),
        ("微信图片_20190704123456.jpg", datetime(2019, 7, 4, 12, 34, 56)),
        ("2021-05-06 07.08.09.jpg", datetime(2021, 5, 6, 7, 8, 9)),
        ("IMG_1234.JPG", None),
        ("DSC00123.JPG", None),
    ]
    for name, expect in cases:
        got = dates.parse_filename(name)
        dt = got[0]["dt_local"] if got else None
        check(f"文件名 {name}", dt == expect, f"期望 {expect} 实际 {dt}")

    got = dates.parse_filename("2021-08-15.jpg")
    check("纯日期文件名", bool(got) and got[0]["dt_local"].date() == datetime(2021, 8, 15).date())


# ---------------------------------------------------------------- 6. 端到端

def test_pipeline():
    section("端到端：模拟 iCloud 下载文件夹")
    scratch = os.path.join(HERE, "_tmp")
    os.makedirs(scratch, exist_ok=True)
    try:
        tmp = tempfile.mkdtemp(prefix="pmfix_", dir=scratch)
        with open(os.path.join(tmp, ".probe"), "wb") as fh:
            fh.write(b"x")
    except OSError as exc:
        print(f"  [跳过] 当前环境不允许在新建目录中写入（{type(exc).__name__}），"
              f"跳过端到端用例；在普通环境下重新运行即可。")
        return
    try:
        download = datetime(2024, 6, 1, 20, 30, 0)
        files = fixtures.build_icloud_folder(tmp, download)
        rows = scanner.scan(tmp)
        by_name = {r.name: r for r in rows}

        check("扫描到 6 个文件", len(rows) == 6, f"实际 {len(rows)}: {sorted(by_name)}")

        r = by_name.get("2019-07-04 12.34.56.jpg")
        check("文件名可推断", r is not None and r.status == scanner.ST_FIX
              and r.target_dt == datetime(2019, 7, 4, 12, 34, 56),
              f"{getattr(r, 'status', None)} {getattr(r, 'target_dt', None)}")

        r = by_name.get("IMG_0001.JPG")
        check("无线索时标为无法推断", r is not None and r.status == scanner.ST_NONE, str(getattr(r, "status", None)))
        check("无线索默认不勾选", r is not None and r.selected is False)

        r = by_name.get("IMG_3333.HEIC")
        check("完整 EXIF 标为已有", r is not None and r.status == scanner.ST_OK, str(getattr(r, "status", None)))
        check("已有时间默认不勾选", r is not None and r.selected is False)

        r = by_name.get("IMG_3333.MOV")
        check("Live Photo 视频继承时间",
              r is not None and r.target_dt == datetime(2021, 3, 4, 5, 6, 7),
              str(getattr(r, "target_dt", None)))

        r = by_name.get("Screenshot_2020-01-02-03-04-05.png")
        check("PNG 截图可推断", r is not None and r.status == scanner.ST_FIX, str(getattr(r, "status", None)))

        # 备份 + 修复
        results = repair.apply(tmp, rows, backup=True)
        ok = [x for x in results if x.ok]
        check("全部修复成功", len(ok) == len(results), f"{len(ok)}/{len(results)}")

        again = {r.name: r for r in scanner.scan(tmp)}
        r = again.get("2019-07-04 12.34.56.jpg")
        check("修复后 JPEG 有拍摄时间",
              r is not None and r.exif_dt == datetime(2019, 7, 4, 12, 34, 56),
              str(getattr(r, "exif_dt", None)))
        r = again.get("IMG_2222.HEIC") if by_name.get("IMG_2222.HEIC").selected else None
        r = again.get("IMG_3333.HEIC")
        check("修复后 HEIC 仍是 2021-03-04",
              r is not None and r.exif_dt == datetime(2021, 3, 4, 5, 6, 7),
              str(getattr(r, "exif_dt", None)))

        # 文件时间
        target = files["filename_jpg"]
        mt = datetime.fromtimestamp(os.path.getmtime(target))
        check("文件时间已改为拍摄时间", abs((mt - datetime(2019, 7, 4, 12, 34, 56)).total_seconds()) <= 2,
              str(mt))

        # 备份存在且可还原
        bak = os.path.join(tmp, repair.BACKUP_DIR)
        check("备份目录存在", os.path.isdir(bak))
        before = open(os.path.join(bak, "2019-07-04 12.34.56.jpg"), "rb").read()
        check("备份内容是原始文件", jpeg.extract_exif(before) is None)

        res = repair.restore(tmp)
        check("还原成功", len(res) > 0 and all(x.ok for x in res))
        restored = open(target, "rb").read()
        check("还原后与备份一致", restored == before)

        # CSV
        from pmfix import report
        csv_path = os.path.join(tmp, "report.csv")
        report.write_csv(csv_path, rows)
        check("CSV 已生成", os.path.getsize(csv_path) > 100)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("PhotoMetaFix 自测开始")
    for fn in (test_tiff, test_jpeg, test_heic, test_png_mp4, test_filenames, test_pipeline):
        try:
            fn()
        except Exception:
            global FAIL
            FAIL += 1
            FAILURES.append(f"{fn.__name__} 抛出异常")
            print(f"  [ERROR] {fn.__name__}")
            traceback.print_exc()
    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    for f in FAILURES:
        print("  - " + f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
