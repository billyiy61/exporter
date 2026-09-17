"""Командный интерфейс Dahua Exporter."""

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime

from .audio_processor import (
    AUDIO_MODES,
    Cancelled,
    FFmpegError,
    VIDEO_CODECS,
    extract,
    probe_stream,
)
from .dahua_client import ConnectionInfo, DahuaClient, DahuaError


def _conn(args) -> ConnectionInfo:
    return ConnectionInfo(args.host, args.port, args.user, args.password)


def cmd_channels(args) -> int:
    client = DahuaClient(_conn(args))
    print("Устройство:", client.test_connection())
    print("Серийник :", client.get_serial())
    print()
    for ch in client.get_channels():
        print(f"  {ch.number}: {ch.name}")
    return 0


def cmd_find(args) -> int:
    client = DahuaClient(_conn(args))
    start = datetime.strptime(args.start, "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(args.end, "%Y-%m-%d %H:%M:%S")

    recs = client.find_recordings(args.channel, start, end)
    if not recs:
        print("Записей не найдено.")
        return 1

    print(f"Найдено записей: {len(recs)}")
    for r in recs:
        print(
            f"  {r.start:%Y-%m-%d %H:%M:%S} — {r.end:%H:%M:%S}  "
            f"{r.file_length / 1024 / 1024:8.1f} МБ  {r.file_path}"
        )
    return 0


def cmd_export(args) -> int:
    client = DahuaClient(_conn(args))
    start = datetime.strptime(args.start, "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(args.end, "%Y-%m-%d %H:%M:%S")

    url = client.build_rtsp_url(args.channel, start, end, subtype=args.subtype)
    print("RTSP:", url)

    # покажем, что реально в потоке
    try:
        info = probe_stream(url)
        print(
            f"Поток: {info.get('codec_name', '?')} "
            f"{info.get('width', '?')}x{info.get('height', '?')}"
        )
    except FFmpegError as e:
        print("Не удалось определить параметры потока:", e)

    duration = (end - start).total_seconds()

    def on_progress(p: float) -> None:
        print(f"\rПрогресс: {p * 100:5.1f}%", end="", flush=True)

    # Ctrl+C ставит этот флаг — extract() заметит его и погасит ffmpeg
    # вместе с дочерними процессами, не оставив висеть RTSP-сессию
    cancel = threading.Event()

    def watch_interrupt() -> None:
        try:
            while not cancel.is_set():
                cancel.wait(0.2)
        except KeyboardInterrupt:
            cancel.set()

    watcher = threading.Thread(target=watch_interrupt, daemon=True)
    watcher.start()

    try:
        res = extract(
            url,
            args.out,
            audio_mode=args.audio,
            video_codec=args.vcodec,
            progress_cb=on_progress,
            duration_hint=duration,
            cancel_event=cancel,
        )
    except Cancelled:
        print()
        print("Выгрузка отменена. Недописанный файл удалён.")
        return 1
    except KeyboardInterrupt:
        cancel.set()
        print()
        print("Прервано пользователем.")
        return 1
    except FFmpegError as e:
        print()
        print("Ошибка выгрузки:", e)
        return 1
    finally:
        cancel.set()

    print()
    print(
        f"Готово: {res.output_path}  "
        f"{res.size_bytes / 1024 / 1024:.1f} МБ"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dahua_exporter.cli", description="Выгрузка записей с Dahua"
    )
    p.add_argument("--host", required=True, help="IP устройства")
    p.add_argument("--port", type=int, default=80, help="HTTP-порт (по умолчанию 80)")
    p.add_argument("--user", default="admin", help="Логин")
    p.add_argument("--password", default=None, help="Пароль (если не задан — спросит)")

    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("channels", help="Показать каналы")
    c.set_defaults(func=cmd_channels)

    f = sub.add_parser("find", help="Найти записи за интервал")
    f.add_argument("--channel", type=int, required=True)
    f.add_argument("--start", required=True, help='"YYYY-MM-DD HH:MM:SS"')
    f.add_argument("--end", required=True, help='"YYYY-MM-DD HH:MM:SS"')
    f.set_defaults(func=cmd_find)

    e = sub.add_parser("export", help="Выгрузить запись в MP4")
    e.add_argument("--channel", type=int, required=True)
    e.add_argument("--start", required=True, help='"YYYY-MM-DD HH:MM:SS"')
    e.add_argument("--end", required=True, help='"YYYY-MM-DD HH:MM:SS"')
    e.add_argument("--out", required=True, help="Куда сохранить (например out.mp4)")
    e.add_argument("--subtype", type=int, default=0,
                   help="0 = основной поток (4K), 1 = субпоток (меньше)")
    e.add_argument("--audio", choices=list(AUDIO_MODES), default="keep",
                   help="Режим звука")
    e.add_argument("--vcodec", choices=list(VIDEO_CODECS), default="copy",
                   help="Кодек видео: copy (быстро) или h264 (играется везде)")
    e.set_defaults(func=cmd_export)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.password:
        import getpass
        args.password = getpass.getpass("Пароль устройства: ")

    try:
        return args.func(args)
    except DahuaError as e:
        print("Ошибка устройства:", e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        print("Прервано пользователем.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
