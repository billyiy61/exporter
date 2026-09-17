"""
Процессор аудио и видео на базе ffmpeg.

Выгружает RTSP-поток с устройства и обрабатывает звук по выбранному режиму.
Опционально перекодирует видео в H.264, чтобы файл игрался встроенными
плеерами Windows без сторонних кодеков.

ОТМЕНА (важно)
--------------
Раньше отмена работала ненадёжно: ffmpeg мог зависнуть на чтении RTSP,
и код навсегда оставался внутри блокирующего `for line in proc.stdout`,
не доходя до проверки флага. Теперь:

  * вывод ffmpeg читает отдельный поток-читатель, а основной цикл
    опрашивает флаг отмены каждые 100 мс;
  * процесс ffmpeg (и все его дочерние процессы) убивается деревом —
    на Windows через `taskkill /F /T`, иначе остаются висящие дети,
    которые держат RTSP-сессию на NVR, и следующая выгрузка не стартует;
  * если изящно не получилось — kill() и ожидание, затем ещё раз проверяем.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

# --- режимы звука ---------------------------------------------------------

AUDIO_MODES = {
    "strip_full": "Удалить звук полностью",
    "strip_left": "Убрать левый канал",
    "strip_right": "Убрать правый канал",
    "keep": "Оставить звук как есть",
}

VIDEO_CODECS = {
    "copy": "Как есть (быстро; HEVC может не играть в Windows)",
    "h264": "H.264 (медленнее; играется везде, разрешение не меняется)",
}

IS_WINDOWS = platform.system() == "Windows"


class FFmpegError(Exception):
    """Ошибка при работе ffmpeg."""


class Cancelled(FFmpegError):
    """Выгрузка отменена пользователем."""


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegError(
            "ffmpeg не найден в PATH.\n"
            "Установи: winget install Gyan.FFmpeg\n"
            "После установки ЗАКРОЙ и ОТКРОЙ терминал заново."
        )
    return path


def find_ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise FFmpegError("ffprobe не найден в PATH (идёт в комплекте с ffmpeg).")
    return path


# --- убийство процесса вместе с детьми ------------------------------------

def _kill_tree(proc: subprocess.Popen) -> None:
    """
    Надёжно гасит ffmpeg и всех его потомков.

    На Windows обычный kill() оставляет дочерние процессы живыми —
    они держат открытой RTSP-сессию на устройстве, и NVR отказывается
    принимать новую. Поэтому используем taskkill с флагом /T (дерево).
    """
    if proc.poll() is not None:
        return  # уже мёртв

    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        except Exception:  # noqa: BLE001
            pass
    else:
        # на Unix: сначала мягко, потом дерево
        try:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass

    # добиваем сам процесс, если ещё жив
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass

    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


# --- фильтры --------------------------------------------------------------

def build_audio_filter(mode: str) -> list[str]:
    if mode == "strip_full":
        return ["-an"]
    if mode == "strip_left":
        return ["-af", "pan=stereo|c0=c1|c1=c1"]   # остаётся правый
    if mode == "strip_right":
        return ["-af", "pan=stereo|c0=c0|c1=c0"]   # остаётся левый
    if mode == "keep":
        return ["-c:a", "aac", "-b:a", "128k"]
    raise ValueError(f"Неизвестный режим звука: {mode}")


def build_video_filter(video_codec: str) -> list[str]:
    if video_codec == "copy":
        return ["-c:v", "copy"]
    if video_codec == "h264":
        return [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]
    raise ValueError(f"Неизвестный кодек: {video_codec}")


# --- проверка потока ------------------------------------------------------

def probe_stream(url: str, timeout: int = 20) -> dict:
    """Узнаёт кодек и разрешение потока. Прерывается по таймауту."""
    ffprobe = find_ffprobe()
    cmd = [ffprobe, "-v", "error"]
    if url.startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp", "-timeout", "10000000"]
    cmd += [
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height",
        "-of", "default=noprint_wrappers=1",
        url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise FFmpegError(
            "Устройство не ответило за 20 секунд. "
            "Проверь интервал — возможно, в нём нет записи."
        ) from None
    info = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    return info


# --- выгрузка -------------------------------------------------------------

@dataclass
class ExtractResult:
    output_path: str
    duration_seconds: float
    size_bytes: int


def extract(
    rtsp_url: str,
    output_path: str,
    audio_mode: str = "keep",
    video_codec: str = "copy",
    progress_cb: Callable[[float], None] | None = None,
    duration_hint: float | None = None,
    cancel_event: threading.Event | None = None,
) -> ExtractResult:
    """
    Выгружает запись в MP4.

    Отменяется в любой момент через cancel_event — включая случай,
    когда ffmpeg молчит и не отдаёт данных.
    """
    ffmpeg = find_ffmpeg()

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if rtsp_url.startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp"]
    cmd += ["-i", rtsp_url]
    cmd += build_video_filter(video_codec)
    cmd += build_audio_filter(audio_mode)
    cmd += ["-progress", "pipe:1", "-nostats", "-flush_packets", "1", output_path]

    # на Unix своя группа процессов — чтобы гасить дерево одним сигналом
    kwargs = {}
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, **kwargs,
    )

    # --- читатель вывода: отдельный поток, чтобы основной не блокировался ---
    lines: list[str] = []
    reader_done = threading.Event()

    def read_stdout() -> None:
        try:
            for line in proc.stdout:            # type: ignore[union-attr]
                lines.append(line)
        except Exception:  # noqa: BLE001
            pass
        finally:
            reader_done.set()

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()

    # --- основной цикл: опрос флага отмены каждые 100 мс -------------------
    out_time = 0.0
    consumed = 0

    while True:
        if cancel_event is not None and cancel_event.is_set():
            _kill_tree(proc)
            _cleanup_partial(output_path)
            raise Cancelled("Выгрузка отменена.")

        # забираем все новые строки прогресса
        while consumed < len(lines):
            line = lines[consumed].strip()
            consumed += 1
            if line.startswith("out_time_ms=") and progress_cb and duration_hint:
                try:
                    out_time = int(line.split("=", 1)[1]) / 1_000_000
                    progress_cb(min(max(out_time / duration_hint, 0.0), 1.0))
                except (ValueError, ZeroDivisionError):
                    pass

        if proc.poll() is not None and reader_done.is_set():
            break

        time.sleep(0.1)

    # допишем оставшиеся строки
    while consumed < len(lines):
        line = lines[consumed].strip()
        consumed += 1
        if line.startswith("out_time_ms=") and progress_cb and duration_hint:
            try:
                out_time = int(line.split("=", 1)[1]) / 1_000_000
                progress_cb(min(max(out_time / duration_hint, 0.0), 1.0))
            except (ValueError, ZeroDivisionError):
                pass

    stderr = ""
    try:
        stderr = proc.stderr.read() if proc.stderr else ""   # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

    if proc.returncode != 0:
        _cleanup_partial(output_path)
        raise FFmpegError(
            f"ffmpeg завершился с кодом {proc.returncode}.\n{stderr[-1500:]}"
        )

    if not os.path.exists(output_path):
        raise FFmpegError("ffmpeg отработал, но файл не создан.")

    size = os.path.getsize(output_path)
    if size == 0:
        _cleanup_partial(output_path)
        raise FFmpegError("Файл создан, но пустой — поток не отдал данных.")

    if progress_cb:
        progress_cb(1.0)

    return ExtractResult(
        output_path=output_path,
        duration_seconds=out_time,
        size_bytes=size,
    )


def _cleanup_partial(path: str) -> None:
    """Удаляет недописанный файл после отмены или ошибки."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
