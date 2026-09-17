"""Смоук-тесты логики, не требующие реального NVR.

Запуск: python -m pytest tests/ -v
или без pytest: python tests/test_logic.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dahua_exporter import audio_processor as ap
from dahua_exporter.dahua_client import DahuaClient, ConnectionInfo, _parse_time


def test_parse_time():
    assert _parse_time("2026-09-15 10:00:00") == datetime(2026, 9, 15, 10, 0, 0)
    print("OK _parse_time")


def test_rtsp_url():
    c = DahuaClient(ConnectionInfo("192.168.1.108", 80, "admin", "p@ss"))
    url = c.build_rtsp_url(
        1, datetime(2026, 9, 15, 10, 0, 0), datetime(2026, 9, 15, 10, 30, 0)
    )
    assert url.startswith("rtsp://admin:p%40ss@192.168.1.108:554/")
    assert "channel=1" in url
    assert "starttime=2026_09_15_10_00_00" in url
    assert "endtime=2026_09_15_10_30_00" in url
    print("OK build_rtsp_url (пароль с @ корректно экранирован)")
    print("   ", url)


def test_parse_find_item():
    block = """
    Channel=1
    StartTime=2026-09-15 10:00:00
    EndTime=2026-09-15 10:05:00
    FilePath=/mnt/sd/2026-09-15/001.dav
    Length=10485760
    Type=dav
    """
    rec = DahuaClient._parse_item(block, channel=1)
    assert rec is not None
    assert rec.start == datetime(2026, 9, 15, 10, 0, 0)
    assert rec.end == datetime(2026, 9, 15, 10, 5, 0)
    assert rec.file_length == 10485760
    assert rec.file_path.endswith("001.dav")
    print("OK _parse_item")


def test_audio_filter_flags():
    # keep_full — копирование без перекодирования
    a = ap._build_filter(ap.AudioMode.KEEP_FULL, None)
    assert "-c:v" in a and "copy" in a and "-c:a" in a
    print("OK keep_full: ремукс, видео и аудио copy")

    # strip_full — видео copy, звук выключен
    a = ap._build_filter(ap.AudioMode.STRIP_FULL, None)
    assert "-an" in a
    print("OK strip_full: -an")

    # keep_range — фильтр volume с enable, порядок -filter_complex до -map
    rng = ap.AudioRange(600, 1200)
    a = ap._build_filter(ap.AudioMode.KEEP_RANGE, rng)
    assert "-filter_complex" in a and "-map" in a and "[a]" in a
    fc = a[a.index("-filter_complex") + 1]
    assert "lt(t,600)" in fc and "gt(t,1200)" in fc
    assert a.index("-filter_complex") < a.index("-map")
    print("OK keep_range: звук только в [600,1200], порядок args верный")

    # strip_range
    a = ap._build_filter(ap.AudioMode.STRIP_RANGE, rng)
    fc = a[a.index("-filter_complex") + 1]
    assert "between(t,600,1200)" in fc
    print("OK strip_range: звук убран в [600,1200]")

    # range-режим без диапазона должен падать
    try:
        ap._build_filter(ap.AudioMode.KEEP_RANGE, None)
        raise AssertionError("ожидалось FfmpegError")
    except ap.FfmpegError:
        pass
    print("OK range без диапазона → FfmpegError")


def test_audio_range_validation_error():
    import shutil
    # ffmpeg в окружении может отсутствовать — ensure_ffmpeg должен
    # бросить понятную ошибку, а не упасть невнятно
    if shutil.which("ffmpeg") is None:
        try:
            ap.ensure_ffmpeg()
            raise AssertionError("ожидалось FfmpegNotFound")
        except ap.FfmpegNotFound as e:
            assert "Установи" in str(e)
            print("OK нет ffmpeg → понятная FfmpegNotFound")
    else:
        print("SKIP ffmpeg есть в PATH")


def run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nВсе {len(tests)} тестов пройдены.")


if __name__ == "__main__":
    run_all()
