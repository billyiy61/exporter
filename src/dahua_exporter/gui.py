"""
Графический интерфейс Dahua Exporter (PySide6).

Структура окна:
  * Подключение — адрес, логин, пароль (пароль в keyring, не в файле)
  * Выгрузка    — канал, интервал, кодек, звук, папка в облаке, два прогресса
  * Аккаунты    — Яндекс.Диск: вход, список аккаунтов, история загрузок

Вкладка «Аккаунты» реализована в cloud_tab.py, диалоги входа и выбора
папок — в cloud_dialogs.py. Здесь только сборка окна и логика выгрузки.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateTimeEdit, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTabWidget, QVBoxLayout, QWidget,
)

from . import theme
from .audio_processor import (
    AUDIO_MODES, Cancelled, FFmpegError, VIDEO_CODECS, extract, probe_stream,
)
from .cloud_tab import CloudTab
from .config import (
    AppConfig, forget_password, get_password, keyring_available,
    load_config, save_config, set_password,
)
from .database import STATE_FAILED, STATE_UPLOADED, Database
from .dahua_client import ConnectionInfo, DahuaClient, DahuaError
from .logging_setup import get_logger
from .yandex_disk import YaDiskClient, YaDiskError

logger = get_logger("gui")


def _icon_path() -> str | None:
    """Ищет icon.ico в корне проекта (assets/icon.ico)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("../../assets/icon.ico", "../../../assets/icon.ico"):
        p = os.path.normpath(os.path.join(here, rel))
        if os.path.exists(p):
            return p
    return None


# --------------------------------------------------------------------------
# Фоновые потоки
# --------------------------------------------------------------------------

class ConnectWorker(QThread):
    """Подключение к регистратору и чтение списка каналов."""

    done = Signal(list, str, str)      # каналы, модель, серийник
    failed = Signal(str)

    def __init__(self, info: ConnectionInfo):
        super().__init__()
        self.info = info

    def run(self) -> None:
        try:
            client = DahuaClient(self.info)
            dtype = client.test_connection()
            serial = client.get_serial()
            channels = client.get_channels()
            self.done.emit(channels, dtype, serial)
        except DahuaError as e:
            self.failed.emit(str(e))
        except Exception as e:                      # noqa: BLE001
            self.failed.emit(f"Неожиданная ошибка: {e}")


class ProbeWorker(QThread):
    """Определяет кодек и разрешение потока до выгрузки."""

    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            info = probe_stream(self.url)
            if not self._cancelled:
                self.done.emit(info)
        except Exception as e:                      # noqa: BLE001
            if not self._cancelled:
                self.failed.emit(str(e))


class ExportWorker(QThread):
    """Выгрузка записи с регистратора в локальный файл."""

    progress = Signal(float)
    done = Signal(str, int)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, url, out, audio_mode, video_codec, duration):
        super().__init__()
        self.url = url
        self.out = out
        self.audio_mode = audio_mode
        self.video_codec = video_codec
        self.duration = duration
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Работает даже если ffmpeg завис и не пишет вывод."""
        self._cancel.set()

    def run(self) -> None:
        try:
            res = extract(
                self.url, self.out,
                audio_mode=self.audio_mode,
                video_codec=self.video_codec,
                progress_cb=self.progress.emit,
                duration_hint=self.duration,
                cancel_event=self._cancel,
            )
            self.done.emit(res.output_path, res.size_bytes)
        except Cancelled:
            self.cancelled.emit()
        except FFmpegError as e:
            if self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(e))
        except Exception as e:                      # noqa: BLE001
            self.failed.emit(f"Неожиданная ошибка: {e}")


class UploadWorker(QThread):
    """Загрузка готового файла в Яндекс.Диск."""

    progress = Signal(float)
    done = Signal(int, str, int)     # upload_id, путь в облаке, размер
    failed = Signal(int, str)        # upload_id, сообщение
    cancelled = Signal(int)

    def __init__(self, client: YaDiskClient, local_path: str,
                 cloud_path: str, upload_id: int):
        super().__init__()
        self.client = client
        self.local_path = local_path
        self.cloud_path = cloud_path
        self.upload_id = upload_id
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            res = self.client.upload_file(
                self.local_path, self.cloud_path,
                on_progress=self.progress.emit,
                cancel_event=self._cancel,
            )
            self.done.emit(self.upload_id, res.cloud_path, res.size)
        except YaDiskError as e:
            if self._cancel.is_set():
                self.cancelled.emit(self.upload_id)
            else:
                self.failed.emit(self.upload_id, str(e))
        except Exception as e:                      # noqa: BLE001
            self.failed.emit(self.upload_id, f"Неожиданная ошибка: {e}")


# --------------------------------------------------------------------------
# Общие элементы оформления
# --------------------------------------------------------------------------

class StatusDot(QLabel):
    """Круглый индикатор: серый / зелёный / красный / жёлтый."""

    COLORS = {
        "idle":  theme.TEXT_MUTE,
        "ok":    theme.SUCCESS,
        "error": theme.DANGER,
        "busy":  theme.WARNING,
    }

    def __init__(self):
        super().__init__()
        self.setFixedSize(10, 10)
        self.set_state("idle")

    def set_state(self, state: str) -> None:
        color = self.COLORS.get(state, self.COLORS["idle"])
        self.setStyleSheet(f"background-color: {color}; border-radius: 5px;")


class Card(QFrame):
    """Панель-карточка с заголовком. Используется и в cloud_tab.py."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        lbl = QLabel(title.upper())
        lbl.setObjectName("cardTitle")
        head.addWidget(lbl)
        head.addStretch()
        self.head = head
        outer.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)

        self.setStyleSheet(f"""
            QFrame#card {{
                background-color: {theme.BG_PANEL};
                border: 1px solid {theme.BORDER};
                border-radius: 12px;
            }}
            QLabel#cardTitle {{
                color: {theme.TEXT_DIM};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
        """)


def field_row(label: str, widget: QWidget, width: int = 96) -> QHBoxLayout:
    """Строка «подпись слева, поле справа»."""
    row = QHBoxLayout()
    row.setSpacing(10)
    lbl = QLabel(label)
    lbl.setFixedWidth(width)
    lbl.setStyleSheet(f"color: {theme.TEXT_DIM};")
    row.addWidget(lbl)
    row.addWidget(widget, 1)
    return row


def compact_row(label: str, widget: QWidget, width: int = 52) -> QHBoxLayout:
    """
    Узкая строка для сетки 2 на 2.

    Ширина подписи меньше, чем в field_row: в узкой колонке
    подпись не должна отъедать место у поля.
    """
    row = QHBoxLayout()
    row.setSpacing(8)
    lbl = QLabel(label)
    lbl.setFixedWidth(width)
    lbl.setStyleSheet(f"color: {theme.TEXT_DIM};")
    row.addWidget(lbl)
    row.addWidget(widget, 1)
    return row


def _select_data(combo: QComboBox, value) -> bool:
    """
    Выбирает в списке пункт по его значению (а не по номеру).

    Нужно там, где важен смысл выбора: порядок ключей в словаре
    может измениться, и привязка к индексу тихо сломает поведение.
    Возвращает True, если пункт найден.
    """
    idx = combo.findData(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
        return True
    return False


def _narrow(card: "Card") -> None:
    """
    Делает карточку узкой: в сетке 2 на 2 панели не должны
    раздуваться на всю ширину окна.
    """
    card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    card.setMinimumWidth(300)


# --------------------------------------------------------------------------
# Главное окно
# --------------------------------------------------------------------------

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dahua Exporter")
        self.resize(920, 760)
        self.setMinimumSize(780, 640)

        ico = _icon_path()
        if ico:
            self.setWindowIcon(QIcon(ico))

        # --- состояние ---
        self.cfg: AppConfig = load_config()
        self.client: DahuaClient | None = None
        self.channels: list = []
        self.export_worker: ExportWorker | None = None
        self.probe_worker: ProbeWorker | None = None
        self.upload_worker: UploadWorker | None = None
        self.last_local_file: str | None = None
        self.last_upload_id: int | None = None
        self._connect_worker: ConnectWorker | None = None

        # --- база ---
        try:
            self.db: Database | None = Database()
        except Exception as e:                      # noqa: BLE001
            self.db = None
            print("Не удалось открыть базу:", e)

        # --- разметка ---
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        root.addWidget(self._build_header())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_connect_tab(), "Подключение")
        self.tabs.addTab(self._build_export_tab(), "Выгрузка")
        self.tabs.addTab(self._build_cloud_tab(), "Аккаунты")
        root.addWidget(self.tabs, 1)

        root.addWidget(self._build_status_bar())

        self._apply_config_to_ui()
        self._update_status("idle", "Не подключено.")

    # ---------------- шапка ----------------

    def _build_header(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(12)

        ico_path = _icon_path()
        if ico_path:
            ico_lbl = QLabel()
            ico_lbl.setPixmap(QIcon(ico_path).pixmap(44, 44))
            lay.addWidget(ico_lbl)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Dahua Exporter")
        title.setStyleSheet(
            f"font-size: 19px; font-weight: 700; color: {theme.TEXT};"
        )
        sub = QLabel("выгрузка записей с NVR/DVR в облако")
        sub.setStyleSheet(f"color: {theme.TEXT_MUTE}; font-size: 12px;")
        titles.addWidget(title)
        titles.addWidget(sub)
        lay.addLayout(titles)
        lay.addStretch()
        return w

    def _build_status_bar(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(8)
        self.status_dot = StatusDot()
        lay.addWidget(self.status_dot)
        self.status_label = QLabel("Не подключено.")
        self.status_label.setStyleSheet(f"color: {theme.TEXT_DIM};")
        lay.addWidget(self.status_label, 1)
        return w

    # ---------------- вкладка «Подключение» ----------------

    def _build_connect_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)

        # --- адрес ---
        card = Card("Устройство")
        row = QHBoxLayout()
        row.setSpacing(10)

        hl = QLabel("IP")
        hl.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("192.168.1.108")

        pl = QLabel("Порт")
        pl.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self.port_edit = QLineEdit("80")
        self.port_edit.setFixedWidth(76)

        row.addWidget(hl)
        row.addWidget(self.host_edit, 1)
        row.addSpacing(8)
        row.addWidget(pl)
        row.addWidget(self.port_edit)
        card.body.addLayout(row)

        hint = QLabel(
            "Нужен HTTP-порт, обычно 80. Порт 37777 — другой протокол, "
            "он здесь не используется."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        card.body.addWidget(hint)
        lay.addWidget(card)

        # --- доступ ---
        card2 = Card("Доступ")
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("admin")
        card2.body.addLayout(field_row("Логин", self.user_edit))

        pw_row = QHBoxLayout()
        pw_row.setSpacing(10)
        pw_lbl = QLabel("Пароль")
        pw_lbl.setFixedWidth(96)
        pw_lbl.setStyleSheet(f"color: {theme.TEXT_DIM};")
        pw_row.addWidget(pw_lbl)

        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.pass_edit.setPlaceholderText("пароль устройства")
        pw_row.addWidget(self.pass_edit, 1)

        self.show_pw_btn = QPushButton("Показать")
        self.show_pw_btn.setCheckable(True)
        self.show_pw_btn.setFixedWidth(96)
        self.show_pw_btn.toggled.connect(self._toggle_password)
        pw_row.addWidget(self.show_pw_btn)
        card2.body.addLayout(pw_row)

        rem_row = QHBoxLayout()
        rem_row.setSpacing(10)
        self.remember_cb = QCheckBox("Запомнить пароль")
        if keyring_available():
            self.remember_cb.setToolTip(
                "Пароль сохранится в системном хранилище Windows "
                "(Диспетчер учётных данных), а не в файле настроек."
            )
        else:
            self.remember_cb.setEnabled(False)
            self.remember_cb.setToolTip("Системное хранилище недоступно.")
        rem_row.addWidget(self.remember_cb)
        rem_row.addStretch()

        self.forget_pw_btn = QPushButton("Забыть пароль")
        self.forget_pw_btn.setFixedHeight(30)
        self.forget_pw_btn.setEnabled(keyring_available())
        self.forget_pw_btn.clicked.connect(self._on_forget_password)
        rem_row.addWidget(self.forget_pw_btn)
        card2.body.addLayout(rem_row)

        if not keyring_available():
            warn = QLabel("Хранилище паролей недоступно: py -m pip install keyring")
            warn.setStyleSheet(f"color: {theme.WARNING}; font-size: 11px;")
            warn.setWordWrap(True)
            card2.body.addWidget(warn)
        lay.addWidget(card2)

        # --- кнопка ---
        self.connect_btn = QPushButton("Подключиться и загрузить камеры")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.setMinimumHeight(42)
        self.connect_btn.clicked.connect(self._on_connect)
        lay.addWidget(self.connect_btn)

        # --- статус ---
        self.connect_status = QLabel("Не подключено.")
        self.connect_status.setObjectName("status")
        self.connect_status.setWordWrap(True)
        lay.addWidget(self.connect_status)

        # --- каналы ---
        card3 = Card("Каналы")
        self.channels_combo = QComboBox()
        self.channels_combo.setMinimumHeight(34)
        card3.body.addWidget(self.channels_combo)
        self.channels_info = QLabel("Каналы ещё не загружены.")
        self.channels_info.setObjectName("hint")
        card3.body.addWidget(self.channels_info)
        lay.addWidget(card3)

        lay.addStretch()
        return w

    def _toggle_password(self, shown: bool) -> None:
        self.pass_edit.setEchoMode(
            QLineEdit.Normal if shown else QLineEdit.Password
        )
        self.show_pw_btn.setText("Скрыть" if shown else "Показать")

    # ---------------- вкладка «Выгрузка» ----------------

    def _build_export_tab(self) -> QWidget:
        """
        Вкладка выгрузки.

        Раскладка — сетка 2 на 2, чтобы всё уместилось в окно без прокрутки:

            [ Запись ]        [ Поток и кодек ]
            [ Звук ]          [ Яндекс.Диск  ]

        Раньше блоки шли в столбик во всю ширину, из-за чего поля
        растягивались и нижняя часть уезжала за пределы окна.
        """
        # содержимое кладём в прокручиваемую область: на маленьком экране
        # окно остаётся usable, а не обрезается
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        w = QWidget()
        scroll.setWidget(w)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)

        # ---------------- сетка 2 на 2 ----------------
        grid = QGridLayout()
        grid.setSpacing(14)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        # ---------- [0,0] ЗАПИСЬ: канал и интервал ----------
        card = Card("Запись")
        _narrow(card)

        self.export_channel = QComboBox()
        self.export_channel.setMinimumHeight(34)
        card.body.addLayout(compact_row("Канал", self.export_channel))

        self.start_edit = QDateTimeEdit(datetime.now() - timedelta(hours=1))
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("dd.MM  HH:mm:ss")
        self.start_edit.setMinimumHeight(34)
        card.body.addLayout(compact_row("С", self.start_edit))

        self.end_edit = QDateTimeEdit(datetime.now())
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("dd.MM  HH:mm:ss")
        self.end_edit.setMinimumHeight(34)
        card.body.addLayout(compact_row("По", self.end_edit))

        quick = QHBoxLayout()
        quick.setSpacing(6)
        for label, hours in [("10 мин", 1 / 6), ("1 ч", 1),
                             ("3 ч", 3), ("24 ч", 24)]:
            b = QPushButton(label)
            b.setFixedHeight(30)
            b.setToolTip(f"Выставить интервал за последние {label}")
            b.clicked.connect(lambda _=False, h=hours: self._set_interval(h))
            quick.addWidget(b)
        quick.addStretch()
        card.body.addLayout(quick)
        grid.addWidget(card, 0, 0)

        # ---------- [0,1] ПОТОК И КОДЕК ----------
        card2 = Card("Поток и кодек")
        _narrow(card2)

        self.subtype_combo = QComboBox()
        self.subtype_combo.addItem("Основной — полное качество", 0)
        self.subtype_combo.addItem("Субпоток — легче и быстрее", 1)
        self.subtype_combo.setMinimumHeight(34)
        card2.body.addLayout(compact_row("Поток", self.subtype_combo))

        self.vcodec_combo = QComboBox()
        for key in VIDEO_CODECS:
            label = ("Перекодировать в H.264" if key == "h264"
                     else "Как есть (без перекода)")
            self.vcodec_combo.addItem(label, key)
        # ставим «как есть» по ЗНАЧЕНИЮ, а не по номеру строки: порядок
        # ключей в VIDEO_CODECS может измениться, и жёсткий индекс
        # незаметно включал бы перекодирование
        _select_data(self.vcodec_combo, "copy")
        self.vcodec_combo.setMinimumHeight(34)
        card2.body.addLayout(compact_row("Видео", self.vcodec_combo))

        self.probe_btn = QPushButton("Проверить поток")
        self.probe_btn.clicked.connect(self._on_probe)
        card2.body.addWidget(self.probe_btn)

        self.stream_lbl = QLabel("Поток не проверен.")
        self.stream_lbl.setObjectName("hint")
        self.stream_lbl.setWordWrap(True)
        card2.body.addWidget(self.stream_lbl)
        card2.body.addStretch()
        grid.addWidget(card2, 0, 1)

        # ---------- [1,0] ЗВУК ----------
        card3 = Card("Звук")
        _narrow(card3)

        self.audio_combo = QComboBox()
        for key, label in AUDIO_MODES.items():
            self.audio_combo.addItem(label, key)
        self.audio_combo.setMinimumHeight(34)
        card3.body.addWidget(self.audio_combo)

        audio_hint = QLabel(
            "Выбери, что делать со звуковой дорожкой записи."
        )
        audio_hint.setObjectName("hint")
        audio_hint.setWordWrap(True)
        card3.body.addWidget(audio_hint)
        card3.body.addStretch()
        grid.addWidget(card3, 1, 0)

        # ---------- [1,1] ЯНДЕКС.ДИСК ----------
        card4 = Card("Яндекс.Диск")
        _narrow(card4)

        self.cloud_cb = QCheckBox("Загрузить в облако после скачивания")
        self.cloud_cb.toggled.connect(self._on_cloud_toggled)
        card4.body.addWidget(self.cloud_cb)

        from .cloud_dialogs import FolderSelector
        self.folder_selector = FolderSelector()
        card4.body.addLayout(compact_row("Папка", self.folder_selector))

        self.cloud_hint = QLabel(
            "Чтобы выбрать папку, войди в аккаунт на вкладке «Аккаунты»."
        )
        self.cloud_hint.setObjectName("hint")
        self.cloud_hint.setWordWrap(True)
        card4.body.addWidget(self.cloud_hint)
        card4.body.addStretch()
        grid.addWidget(card4, 1, 1)

        # одинаковая ширина колонок, панели тянутся по содержимому
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 0)
        grid.setRowStretch(1, 0)

        lay.addLayout(grid)

        # ---------------- прогресс и кнопки ----------------
        prog_card = Card("Ход выгрузки")
        prog_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        prog = QVBoxLayout()
        prog.setSpacing(6)

        self.nvr_lbl = QLabel("Скачивание с регистратора")
        self.nvr_lbl.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")
        self.progress_nvr = QProgressBar()
        self.progress_nvr.setValue(0)
        prog.addWidget(self.nvr_lbl)
        prog.addWidget(self.progress_nvr)

        self.cloud_lbl = QLabel("Загрузка в облако")
        self.cloud_lbl.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")
        self.progress_cloud = QProgressBar()
        self.progress_cloud.setValue(0)
        prog.addWidget(self.cloud_lbl)
        prog.addWidget(self.progress_cloud)
        prog_card.body.addLayout(prog)

        row_btn = QHBoxLayout()
        row_btn.setSpacing(10)
        self.export_btn = QPushButton("Выгрузить")
        self.export_btn.setObjectName("primary")
        self.export_btn.setMinimumHeight(42)
        self.export_btn.clicked.connect(self._on_export)
        row_btn.addWidget(self.export_btn, 1)

        self.cancel_btn = QPushButton("Отменить")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setMinimumHeight(42)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        row_btn.addWidget(self.cancel_btn)

        # «Повторить» появляется, только если загрузка в облако упала.
        # Позволяет взять уже скачанный файл с диска (или выбрать другой)
        # и залить заново, НЕ скачивая запись с регистратора повторно.
        self.retry_btn = QPushButton("Повторить загрузку")
        self.retry_btn.setObjectName("primary")
        self.retry_btn.setMinimumHeight(42)
        self.retry_btn.setVisible(False)
        self.retry_btn.setToolTip(
            "Загрузить в облако ещё раз, не скачивая запись заново"
        )
        self.retry_btn.clicked.connect(self._on_retry_upload)
        row_btn.addWidget(self.retry_btn)
        prog_card.body.addLayout(row_btn)

        self.log_lbl = QLabel("Готов к выгрузке.")
        self.log_lbl.setWordWrap(True)
        self.log_lbl.setStyleSheet(f"color: {theme.TEXT_DIM};")
        prog_card.body.addWidget(self.log_lbl)

        lay.addWidget(prog_card)
        lay.addStretch()
        return scroll

    def _set_interval(self, hours: float) -> None:
        end = self.end_edit.dateTime().toPython()
        self.start_edit.setDateTime(end - timedelta(hours=hours))

    # ---------------- вкладка «Аккаунты» ----------------

    def _build_cloud_tab(self) -> QWidget:
        if self.db is None:
            w = QWidget()
            lay = QVBoxLayout(w)
            msg = QLabel(
                "Не удалось открыть базу данных.\n"
                "История загрузок и аккаунты недоступны.\n"
                "Проверь права на папку ~/.dahua_exporter"
            )
            msg.setWordWrap(True)
            msg.setStyleSheet(f"color: {theme.DANGER};")
            lay.addWidget(msg)
            lay.addStretch()
            return w

        from .cloud_tab import CloudTab
        self.cloud_tab = CloudTab(self.db)
        # когда аккаунт сменился — обновляем доступность выбора папки
        try:
            self.cloud_tab.accounts_combo.currentIndexChanged.connect(
                self._sync_cloud_client
            )
        except Exception:                            # noqa: BLE001
            pass
        return self.cloud_tab

    def _sync_cloud_client(self) -> None:
        """Передаёт текущий клиент Яндекс.Диска в селектор папок."""
        if not hasattr(self, "folder_selector"):
            return
        client = None
        try:
            _, client = self.cloud_tab.get_active_client()
        except Exception:                            # noqa: BLE001
            client = None
        self.folder_selector.set_client(client)
        if client is not None:
            self.cloud_hint.setStyleSheet(
                f"color: {theme.SUCCESS}; font-size: 11px;"
            )
            self.cloud_hint.setText("Аккаунт активен, можно выбирать папку.")
        else:
            self.cloud_hint.setStyleSheet(
                f"color: {theme.TEXT_MUTE}; font-size: 11px;"
            )
            self.cloud_hint.setText(
                "Чтобы выбрать папку, войди в аккаунт на вкладке «Аккаунты»."
            )

    def _on_cloud_toggled(self, checked: bool) -> None:
        if checked and self.db is not None:
            self._sync_cloud_client()

    # ---------------- конфиг ----------------

    def _apply_config_to_ui(self) -> None:
        self.host_edit.setText(self.cfg.host)
        self.port_edit.setText(str(self.cfg.port))
        self.user_edit.setText(self.cfg.user)

        idx = self.subtype_combo.findData(self.cfg.subtype)
        if idx >= 0:
            self.subtype_combo.setCurrentIndex(idx)

        idx = self.vcodec_combo.findData(self.cfg.video_codec)
        if idx >= 0:
            self.vcodec_combo.setCurrentIndex(idx)

        idx = self.audio_combo.findData(self.cfg.audio_mode)
        if idx >= 0:
            self.audio_combo.setCurrentIndex(idx)

        self.remember_cb.setChecked(self.cfg.remember_password)

        # облако и папка: раньше эти строки отсутствовали, и после
        # перезапуска галочка всегда была снята, а путь сбрасывался
        self.cloud_cb.setChecked(self.cfg.cloud_enabled)
        if hasattr(self, "folder_selector") and self.cfg.cloud_folder:
            self.folder_selector.edit.setText(self.cfg.cloud_folder)
        self._on_cloud_toggled(self.cfg.cloud_enabled)

        if self.cfg.remember_password:
            pw = get_password(self.cfg.user)
            if pw:
                self.pass_edit.setText(pw)

    def _collect_config(self) -> AppConfig:
        c = AppConfig()
        c.host = self.host_edit.text().strip()
        try:
            c.port = int(self.port_edit.text().strip())
        except ValueError:
            c.port = 80
        c.user = self.user_edit.text().strip() or "admin"
        c.subtype = self.subtype_combo.currentData()
        c.video_codec = self.vcodec_combo.currentData()
        c.audio_mode = self.audio_combo.currentData()
        c.remember_password = self.remember_cb.isChecked()
        c.last_out_dir = self.cfg.last_out_dir
        c.cloud_enabled = self.cloud_cb.isChecked()
        if hasattr(self, "folder_selector"):
            c.cloud_folder = self.folder_selector.path()
        return c

    def _persist(self) -> None:
        self.cfg = self._collect_config()
        save_config(self.cfg)
        if self.cfg.remember_password:
            set_password(self.cfg.user, self.pass_edit.text())
        else:
            set_password(self.cfg.user, "")

    # ---------------- статус ----------------

    def _update_status(self, state: str, text: str) -> None:
        colors = {
            "idle":  theme.TEXT_MUTE,
            "ok":    theme.SUCCESS,
            "error": theme.DANGER,
            "busy":  theme.WARNING,
        }
        if hasattr(self, "status_dot"):
            self.status_dot.set_state(state)
        if hasattr(self, "status_label"):
            self.status_label.setText(text)
            self.status_label.setStyleSheet(
                f"color: {colors.get(state, theme.TEXT_MUTE)};"
            )

    # ---------------- подключение ----------------

    def _on_forget_password(self) -> None:
        user = self.user_edit.text().strip() or "admin"
        forget_password(user)
        self.pass_edit.clear()
        self.remember_cb.setChecked(False)
        self.cfg.remember_password = False
        save_config(self.cfg)
        QMessageBox.information(
            self, "Пароль удалён",
            f"Сохранённый пароль для «{user}» удалён."
        )

    def _on_connect(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Адрес", "Укажи IP устройства.")
            return
        try:
            port = int(self.port_edit.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Порт", "Порт должен быть числом.")
            return

        self._persist()
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("Подключаюсь…")
        self._update_status("busy", "подключение…")
        self.connect_status.setText("Подключаюсь к устройству…")

        info = ConnectionInfo(
            host, port,
            self.user_edit.text().strip(),
            self.pass_edit.text(),
        )
        self._connect_worker = ConnectWorker(info)
        self._connect_worker.done.connect(self._on_connected)
        self._connect_worker.failed.connect(self._on_connect_failed)
        self._connect_worker.start()

    def _on_connected(self, channels, dtype, serial) -> None:
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Подключиться и загрузить камеры")

        self.client = DahuaClient(ConnectionInfo(
            self.host_edit.text().strip(),
            int(self.port_edit.text().strip()),
            self.user_edit.text().strip(),
            self.pass_edit.text(),
        ))
        self.channels = channels

        self.channels_combo.clear()
        self.export_channel.clear()
        for ch in channels:
            self.channels_combo.addItem(f"{ch.number} · {ch.name}", ch.number)
            self.export_channel.addItem(f"{ch.number} · {ch.name}", ch.number)

        self.channels_info.setText(f"Загружено каналов: {len(channels)}")
        self.connect_status.setText(
            f"Подключено.\nУстройство: {dtype}\n"
            f"Серийник: {serial}\nКаналов: {len(channels)}"
        )
        self._update_status("ok", "подключено")

    def _on_connect_failed(self, msg: str) -> None:
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Подключиться и загрузить камеры")
        self._update_status("error", "ошибка подключения")
        self.connect_status.setText("Ошибка подключения.")
        QMessageBox.critical(self, "Ошибка подключения", msg)

    # ---------------- выгрузка ----------------

    def _current_rtsp_url(self, channel: int | None = None,
                          short: bool = False) -> str | None:
        if self.client is None:
            QMessageBox.warning(
                self, "Нет подключения",
                "Сначала подключись на вкладке «Подключение»."
            )
            return None
        if channel is None:
            channel = self.export_channel.currentData()
        if channel is None:
            QMessageBox.warning(self, "Канал", "Канал не выбран.")
            return None

        start = self.start_edit.dateTime().toPython()
        end = self.end_edit.dateTime().toPython()
        if short:
            end = min(start + timedelta(minutes=1), end)
        if end <= start:
            QMessageBox.warning(self, "Интервал", "Конец раньше начала.")
            return None

        subtype = self.subtype_combo.currentData()
        return self.client.build_rtsp_url(channel, start, end, subtype=subtype)

    def _on_probe(self) -> None:
        url = self._current_rtsp_url(short=True)
        if not url:
            return
        self.probe_btn.setEnabled(False)
        self.probe_btn.setText("Проверяю…")
        self.stream_lbl.setStyleSheet(f"color: {theme.TEXT_MUTE}; font-size: 11px;")
        self.stream_lbl.setText("Определяю кодек и разрешение…")

        self.probe_worker = ProbeWorker(url)
        self.probe_worker.done.connect(self._on_probe_done)
        self.probe_worker.failed.connect(self._on_probe_failed)
        self.probe_worker.start()

    def _on_probe_done(self, info: dict) -> None:
        self.probe_btn.setEnabled(True)
        self.probe_btn.setText("Проверить поток")
        codec = (info.get("codec_name") or "?").lower()
        w = info.get("width", "?")
        h = info.get("height", "?")
        msg = f"Кодек: {codec.upper()} · {w}×{h}"
        if codec in ("hevc", "h265"):
            self.stream_lbl.setStyleSheet(
                f"color: {theme.WARNING}; font-size: 11px;"
            )
            self.stream_lbl.setText(
                msg + "  · HEVC не откроется в стандартном плеере Windows — "
                "выбери «Перекодировать в H.264» или смотри через VLC"
            )
        else:
            self.stream_lbl.setStyleSheet(
                f"color: {theme.SUCCESS}; font-size: 11px;"
            )
            self.stream_lbl.setText(msg + "  · откроется в обычном плеере")

    def _on_probe_failed(self, msg: str) -> None:
        self.probe_btn.setEnabled(True)
        self.probe_btn.setText("Проверить поток")
        self.stream_lbl.setStyleSheet(f"color: {theme.DANGER}; font-size: 11px;")
        self.stream_lbl.setText(f"Не удалось проверить поток: {msg}")

    def _on_export(self) -> None:
        url = self._current_rtsp_url()
        if not url:
            return

        start = self.start_edit.dateTime().toPython()
        end = self.end_edit.dateTime().toPython()
        duration = (end - start).total_seconds()

        # куда сохранить локально
        default_dir = self.cfg.last_out_dir or os.path.expanduser("~")
        default_name = os.path.join(
            default_dir,
            f"ch{self.export_channel.currentData()}_{start:%Y%m%d_%H%M%S}.mp4",
        )
        out, _ = QFileDialog.getSaveFileName(
            self, "Куда сохранить", default_name, "Видео MP4 (*.mp4)"
        )
        if not out:
            return

        self.cfg.last_out_dir = os.path.dirname(out)
        save_config(self.cfg)

        # проверяем облако заранее — чтобы не гонять выгрузку зря
        cloud_client = None
        cloud_path = None
        if self.cloud_cb.isChecked():
            if self.db is None:
                QMessageBox.warning(
                    self, "Облако недоступно",
                    "База данных не открыта, загрузка в облако невозможна."
                )
                return
            try:
                account_id, cloud_client = self.cloud_tab.get_active_client()
            except Exception:                        # noqa: BLE001
                account_id, cloud_client = None, None
            if cloud_client is None or account_id is None:
                QMessageBox.warning(
                    self, "Облако недоступно",
                    "Не выбран активный аккаунт Яндекс.Диска.\n"
                    "Войди в аккаунт на вкладке «Аккаунты»."
                )
                return
            folder = self.folder_selector.path().rstrip("/")
            fname = os.path.basename(out)
            cloud_path = f"{folder}/{fname}"

        # готовим запись в истории
        upload_id = None
        if self.db is not None:
            ch_name = self.export_channel.currentText()
            upload_id = self.db.add_upload(
                account_id=(account_id if cloud_path else None),
                device_host=self.host_edit.text().strip(),
                channel=int(self.export_channel.currentData() or 0),
                channel_name=ch_name,
                rec_start=start.strftime("%Y-%m-%d %H:%M:%S"),
                rec_end=end.strftime("%Y-%m-%d %H:%M:%S"),
                local_path=out,
                cloud_path=cloud_path or "",
            )

        self.last_local_file = out
        self.last_upload_id = upload_id
        self._pending_cloud = (cloud_client, cloud_path)

        self.export_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Отменить")
        self.progress_nvr.setValue(0)
        self.progress_cloud.setValue(0)
        self._update_status("busy", "выгрузка…")
        self.log_lbl.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self.log_lbl.setText("Скачиваю запись с регистратора…")

        self.export_worker = ExportWorker(
            url, out,
            self.audio_combo.currentData(),
            self.vcodec_combo.currentData(),
            duration,
        )
        self.export_worker.progress.connect(
            lambda p: self.progress_nvr.setValue(int(p * 100))
        )
        self.export_worker.done.connect(self._on_export_done)
        self.export_worker.failed.connect(self._on_export_failed)
        self.export_worker.cancelled.connect(self._on_export_cancelled)
        self.export_worker.finished.connect(self._release_export_worker)
        self.export_worker.start()

    def _release_export_worker(self) -> None:
        if self.export_worker is not None:
            self.export_worker.deleteLater()
            self.export_worker = None

    def _on_export_done(self, path: str, size: int) -> None:
        self.progress_nvr.setValue(100)
        self.log_lbl.setStyleSheet(f"color: {theme.SUCCESS};")
        self.log_lbl.setText(
            f"Скачано: {os.path.basename(path)}  ·  {size / 1024 / 1024:.1f} МБ"
        )

        if self.db is not None and self.last_upload_id:
            self.db.update_upload(
                self.last_upload_id,
                local_path=path, local_size=size, local_exists=1,
            )

        client, cloud_path = getattr(self, "_pending_cloud", (None, None))
        if client is None or not cloud_path:
            # облако не запрашивали — просто скачали файл
            self._finish_export_ui("готово")
            return

        # активный аккаунт нужен для записи в историю
        try:
            account_id, _ = self.cloud_tab.get_active_client()
        except Exception:                            # noqa: BLE001
            account_id = None

        # этап 2 — загрузка в облако
        self.log_lbl.setText(
            self.log_lbl.text() + "  ·  Загружаю в облако…"
        )
        if self.db is not None and self.last_upload_id:
            self.db.update_upload(self.last_upload_id,
                                  upload_state="uploading")

        # Запуск заливки — через общий метод, тот же, что у кнопки
        # «Повторить». Так логика загрузки живёт в одном месте.
        self._start_cloud_upload(client, account_id, path, cloud_path)

    def _on_upload_done(self, upload_id: int, cloud_path: str, size: int) -> None:
        self.progress_cloud.setValue(100)
        if self.db is not None and upload_id:
            self.db.update_upload(
                upload_id,
                upload_state=STATE_UPLOADED,
                cloud_path=cloud_path,
                cloud_size=size,
                uploaded_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        try:
            self.cloud_tab.refresh_all()
        except Exception:                            # noqa: BLE001
            pass

        self.retry_btn.setVisible(False)

        # Даём прогнозу постоять на 100% и показываем итог пользователю.
        # Раньше об успехе говорила только мелкая строка внизу —
        # легко было не заметить.
        self.log_lbl.setStyleSheet(f"color: {theme.SUCCESS};")
        self.log_lbl.setText(
            f"Загружено в облако: {os.path.basename(cloud_path)}"
        )
        self._finish_export_ui("готово")

        QMessageBox.information(
            self, "Загрузка завершена",
            f"Видео успешно загружено в Яндекс.Диск.\n\n"
            f"Файл: {os.path.basename(cloud_path)}\n"
            f"Размер: {size / 1024 / 1024:.1f} МБ\n"
            f"Папка: {cloud_path.rsplit('/', 1)[0] or '/'}"
        )

        # Гасим прогрессы ПОСЛЕ закрытия окна: если сбросить раньше,
        # пользователь увидит пустые полосы за спиной у сообщения.
        QTimer.singleShot(400, self._reset_progress_bars)

    def _on_upload_failed(self, upload_id: int, msg: str) -> None:
        if self.db is not None and upload_id:
            self.db.update_upload(
                upload_id, upload_state=STATE_FAILED, upload_error=msg
            )
        self.progress_cloud.setValue(0)
        self.log_lbl.setStyleSheet(f"color: {theme.DANGER};")
        self.log_lbl.setText(
            "Файл скачан, но в облако не залился.\n" + msg
        )
        self._finish_export_ui("ошибка загрузки в облако")

        # Файл уже на диске — показываем кнопку повтора, чтобы не
        # скачивать запись с регистратора заново
        self.retry_btn.setVisible(True)
        self.export_btn.setEnabled(True)

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Загрузка в облако не удалась")
        box.setText("Файл скачан на компьютер, но в облако не залился.")
        box.setInformativeText(msg)
        retry = box.addButton("Повторить", QMessageBox.AcceptRole)
        box.addButton("Закрыть", QMessageBox.RejectRole)
        box.exec()

        if box.clickedButton() is retry:
            self._on_retry_upload()

    def _on_upload_cancelled(self, upload_id: int) -> None:
        if self.db is not None and upload_id:
            self.db.update_upload(upload_id, upload_state=STATE_FAILED,
                                  upload_error="отменено пользователем")
        self.progress_cloud.setValue(0)
        self.log_lbl.setStyleSheet(f"color: {theme.WARNING};")
        self.log_lbl.setText(
            "Загрузка в облако отменена. Локальный файл остался на диске."
        )
        self._finish_export_ui("отменено")

    def _reset_progress_bars(self) -> None:
        """Обнуляет оба прогресса и возвращает подписи в исходный вид."""
        self.progress_nvr.setValue(0)
        self.progress_cloud.setValue(0)
        self.nvr_lbl.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px;"
        )
        self.cloud_lbl.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px;"
        )
        self.nvr_lbl.setText("Скачивание с регистратора")
        self.cloud_lbl.setText("Загрузка в облако")

    # ---------- повторная загрузка в облако ----------

    def _on_retry_upload(self) -> None:
        """
        Повтор загрузки в облако БЕЗ нового скачивания с регистратора.

        Зачем: если запись уже лежит на диске, гонять её с камеры
        повторно бессмысленно — это минуты ожидания и нагрузка на
        регистратор. Берём готовый файл и заливаем заново.

        Предлагаем на выбор:
          * уже скачанный файл (если он на месте)
          * любой другой файл с диска
        """
        client, account_id, cloud_path = self._retry_context()
        if client is None:
            return

        # Предлагаем локальный файл, если он ещё существует
        local = self.last_local_file
        if local and not os.path.exists(local):
            local = None

        if local:
            ans = QMessageBox.question(
                self, "Повторить загрузку",
                f"Загрузить в облако ещё раз?\n\n"
                f"Файл: {os.path.basename(local)}\n"
                f"Скачивать с регистратора заново НЕ нужно.\n\n"
                f"«Нет» — выбрать другой файл на компьютере.",
            )
            if ans == QMessageBox.Yes:
                self._start_cloud_upload(client, account_id, local, cloud_path)
                return

        # Выбор файла вручную
        start_dir = os.path.dirname(local) if local else (
            self.cfg.last_out_dir or os.path.expanduser("~")
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "Какой файл загрузить в облако", start_dir,
            "Видео MP4 (*.mp4);;Все файлы (*)",
        )
        if not path:
            return

        # Имя в облаке берём по выбранному файлу, папку оставляем прежнюю
        folder = cloud_path.rsplit("/", 1)[0] if cloud_path else ""
        if not folder:
            folder = self.folder_selector.path().rstrip("/") or "/DahuaExporter"
        target = f"{folder}/{os.path.basename(path)}"

        self._start_cloud_upload(client, account_id, path, target)

    def _retry_context(self):
        """
        Собирает всё нужное для повтора: клиент, аккаунт, путь в облаке.

        Возвращает (None, None, None), если повторить невозможно, —
        и объясняет причину пользователю.
        """
        if self.db is None:
            QMessageBox.warning(
                self, "Повтор невозможен",
                "База данных не открыта, история загрузок недоступна."
            )
            return None, None, None

        try:
            account_id, client = self.cloud_tab.get_active_client()
        except Exception:                            # noqa: BLE001
            account_id, client = None, None

        if client is None or account_id is None:
            QMessageBox.warning(
                self, "Повтор невозможен",
                "Нет активного аккаунта Яндекс.Диска.\n"
                "Войди в аккаунт на вкладке «Аккаунты»."
            )
            return None, None, None

        # Путь в облаке берём из последней попытки
        cloud_path = ""
        if self.last_upload_id:
            try:
                rec = self.db.get_upload(self.last_upload_id)
                if rec:
                    cloud_path = rec.cloud_path or ""
            except Exception:                        # noqa: BLE001
                cloud_path = ""

        if not cloud_path:
            folder = self.folder_selector.path().rstrip("/") or "/DahuaExporter"
            local_name = os.path.basename(self.last_local_file or "")
            cloud_path = f"{folder}/{local_name}" if local_name else ""

        return client, account_id, cloud_path

    def _start_cloud_upload(self, client, account_id: int,
                            local_path: str, cloud_path: str) -> None:
        """Общая точка запуска заливки в облако (и для повтора тоже)."""
        if not cloud_path:
            QMessageBox.warning(
                self, "Повтор невозможен",
                "Не удалось определить папку на Яндекс.Диске."
            )
            return

        if not os.path.exists(local_path):
            QMessageBox.warning(
                self, "Файл не найден",
                f"Локальный файл недоступен:\n{local_path}\n\n"
                f"Скачай запись заново или выбери другой файл."
            )
            return

        size = os.path.getsize(local_path)
        if size == 0:
            QMessageBox.warning(
                self, "Файл пустой",
                "Выбранный файл имеет нулевой размер — загружать нечего."
            )
            return

        # История: если прежней записи нет (например, заливаем сторонний
        # файл) — заводим новую строку, чтобы загрузка не потерялась.
        upload_id = self.last_upload_id
        if upload_id is None:
            upload_id = self.db.add_upload(
                account_id=account_id,
                device_host=self.host_edit.text().strip(),
                channel=int(self.export_channel.currentData() or 0),
                channel_name=self.export_channel.currentText(),
                rec_start="", rec_end="",
                local_path=local_path,
                cloud_path=cloud_path,
            )
            self.last_upload_id = upload_id

        self.last_local_file = local_path
        self.retry_btn.setVisible(False)
        self.export_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Отменить")
        self.progress_cloud.setValue(0)

        self.db.update_upload(upload_id, upload_state="uploading",
                              cloud_path=cloud_path,
                              local_path=local_path, local_size=size)

        self._update_status("busy", "загрузка в облако…")
        self.log_lbl.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self.log_lbl.setText(
            f"Загружаю в облако: {os.path.basename(local_path)}"
        )

        self.upload_worker = UploadWorker(
            client, local_path, cloud_path, upload_id or 0
        )
        self.upload_worker.progress.connect(
            lambda p: self.progress_cloud.setValue(int(p * 100))
        )
        self.upload_worker.done.connect(self._on_upload_done)
        self.upload_worker.failed.connect(self._on_upload_failed)
        self.upload_worker.cancelled.connect(self._on_upload_cancelled)
        self.upload_worker.start()

    def _finish_export_ui(self, status_text: str) -> None:
        self.export_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Отменить")
        self._update_status("ok" if status_text == "готово" else "idle",
                            status_text)

    def _on_export_failed(self, msg: str) -> None:
        if self.db is not None and self.last_upload_id:
            self.db.update_upload(self.last_upload_id,
                                  upload_state=STATE_FAILED,
                                  upload_error=msg)
        self.export_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Отменить")
        self._update_status("error", "ошибка выгрузки")
        self.log_lbl.setStyleSheet(f"color: {theme.DANGER};")
        self.log_lbl.setText("Ошибка выгрузки.")
        QMessageBox.critical(self, "Ошибка выгрузки", msg)

    def _on_export_cancelled(self) -> None:
        if self.db is not None and self.last_upload_id:
            self.db.update_upload(self.last_upload_id,
                                  upload_state=STATE_FAILED,
                                  upload_error="отменено пользователем")
        self.export_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Отменить")
        self.progress_nvr.setValue(0)
        self._update_status("idle", "отменено")
        self.log_lbl.setStyleSheet(f"color: {theme.WARNING};")
        self.log_lbl.setText(
            "Выгрузка отменена. Недописанный файл удалён, "
            "можно запускать снова."
        )

    def _on_cancel(self) -> None:
        # отмена относится и к скачиванию, и к загрузке в облако
        if self.upload_worker is not None and self.upload_worker.isRunning():
            self.upload_worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("Отмена…")
            self.log_lbl.setText("Останавливаю загрузку в облако…")
            return

        if self.export_worker is not None and self.export_worker.isRunning():
            self.export_worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("Отмена…")
            self.log_lbl.setStyleSheet(f"color: {theme.WARNING};")
            self.log_lbl.setText("Останавливаю ffmpeg…")
            return

        if self.probe_worker is not None and self.probe_worker.isRunning():
            self.probe_worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("Отмена…")
            self.stream_lbl.setText("Проверка прервана.")
            self.probe_worker.finished.connect(self._reset_after_probe_cancel)

    def _reset_after_probe_cancel(self) -> None:
        self.probe_btn.setEnabled(True)
        self.probe_btn.setText("Проверить поток")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Отменить")

    # ---------------- закрытие ----------------

    def closeEvent(self, event) -> None:
        # гасим всё, что работает, иначе останутся висящие процессы
        for worker in (self.upload_worker, self.export_worker):
            if worker is not None and worker.isRunning():
                worker.cancel()
                worker.wait(6000)
        if self.probe_worker is not None and self.probe_worker.isRunning():
            self.probe_worker.cancel()
            self.probe_worker.wait(3000)
        try:
            self._persist()
        except Exception:                            # noqa: BLE001
            pass
        if self.db is not None:
            try:
                self.db.close()
            except Exception:                        # noqa: BLE001
                pass
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.STYLESHEET)
    app.setApplicationName("Dahua Exporter")
    app.setApplicationDisplayName("Dahua Exporter")

    ico = _icon_path()
    if ico:
        app.setWindowIcon(QIcon(ico))

    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
