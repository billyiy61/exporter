"""
Вкладка «Облако»: аккаунты Яндекс.Диска и история загрузок.

Здесь:
  * список аккаунтов с переключением активного
  * кнопки «Войти», «Выйти», «Удалить»
  * сводка по Диску: сколько занято, сколько свободно
  * история загрузок с фильтром
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from . import theme
from .cloud_dialogs import LoginDialog
from .database import (
    STATE_FAILED, STATE_PENDING, STATE_UPLOADED, STATE_UPLOADING,
    Database,
)
from .yandex_disk import YaDiskClient, YaDiskError


def human_size(n: int) -> str:
    if not n:
        return "—"
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1024
    return f"{n:.1f} ПБ"


STATE_LABELS = {
    STATE_PENDING: "в очереди",
    "downloading": "скачивается",
    STATE_UPLOADING: "загружается",
    STATE_UPLOADED: "загружено",
    STATE_FAILED: "ошибка",
}

STATE_COLORS = {
    STATE_UPLOADED: theme.SUCCESS,
    STATE_FAILED: theme.DANGER,
    STATE_UPLOADING: theme.WARNING,
    "downloading": theme.WARNING,
    STATE_PENDING: theme.TEXT_MUTE,
}


class DiskInfoWorker(QThread):
    """Узнаёт, сколько места на Диске. Отдельным потоком — сеть медленная."""
    # Размер Диска в байтах не влезает в 32-битный int — Qt бросает
    # OverflowError. "qint64" — это 64 бита, до 8 эксабайт.
    done = Signal(str, "qint64", "qint64", str)
    failed = Signal(str)

    def __init__(self, token: str, account_id: int):
        super().__init__()
        self.token = token
        self.account_id = account_id

    def run(self) -> None:
        try:
            client = YaDiskClient(self.token)
            info = client.get_info()
            self.done.emit(info.login, int(info.used_space or 0),
                           int(info.total_space or 0), info.display_name)
        except YaDiskError as e:
            self.failed.emit(f"{self.account_id}|{e}")
        except Exception as e:            # noqa: BLE001
            self.failed.emit(f"{self.account_id}|{e}")


class CloudTab(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.clients: dict[int, YaDiskClient] = {}   # account_id -> client
        self.space_cache: dict[int, tuple[int, int]] = {}
        self._info_worker = None
        self._login_dialog = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)

        from .gui import Card, field_row   # локальный импорт: избегаем цикла

        # ---------- аккаунты ----------
        card = Card("Аккаунты Яндекса")

        self.accounts_combo = QComboBox()
        self.accounts_combo.setMinimumHeight(34)
        self.accounts_combo.currentIndexChanged.connect(self._on_account_changed)
        card.body.addLayout(field_row("Активный", self.accounts_combo))

        btns = QHBoxLayout()
        btns.setSpacing(8)

        self.login_btn = QPushButton("Войти в аккаунт")
        self.login_btn.setObjectName("primary")
        self.login_btn.clicked.connect(self._on_login)
        btns.addWidget(self.login_btn)

        self.logout_btn = QPushButton("Выйти")
        self.logout_btn.clicked.connect(self._on_logout)
        btns.addWidget(self.logout_btn)

        self.remove_btn = QPushButton("Удалить аккаунт")
        self.remove_btn.setObjectName("danger")
        self.remove_btn.clicked.connect(self._on_remove)
        btns.addWidget(self.remove_btn)

        card.body.addLayout(btns)

        self.account_info = QLabel("Аккаунты не добавлены.")
        self.account_info.setObjectName("hint")
        self.account_info.setWordWrap(True)
        card.body.addWidget(self.account_info)
        lay.addWidget(card)

        # ---------- история ----------
        card2 = Card("История загрузок")

        filt = QHBoxLayout()
        filt.setSpacing(8)
        fl = QLabel("Показывать:")
        fl.setStyleSheet(f"color: {theme.TEXT_DIM};")
        filt.addWidget(fl)

        self.filter_combo = QComboBox()
        self.filter_combo.addItem("Все загрузки", "all")
        self.filter_combo.addItem("Только загруженные", STATE_UPLOADED)
        self.filter_combo.addItem("Только с ошибкой", STATE_FAILED)
        self.filter_combo.currentIndexChanged.connect(self.refresh_history)
        filt.addWidget(self.filter_combo, 1)

        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.setFixedWidth(110)
        self.refresh_btn.clicked.connect(self.refresh_all)
        filt.addWidget(self.refresh_btn)
        card2.body.addLayout(filt)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Канал", "Интервал записи", "Размер", "Статус", "Аккаунт", "Залито"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        card2.body.addWidget(self.table)

        self.history_info = QLabel("")
        self.history_info.setObjectName("hint")
        card2.body.addWidget(self.history_info)

        lay.addWidget(card2, 1)

        self.refresh_accounts()
        self.refresh_history()

    # ---------- аккаунты ----------

    def refresh_accounts(self) -> None:
        """Перечитывает аккаунты из базы и обновляет список."""
        current = self.accounts_combo.currentData()
        self.accounts_combo.blockSignals(True)
        self.accounts_combo.clear()

        accounts = self.db.list_accounts()
        logged_in = [a for a in accounts if a.access_token]

        if not logged_in:
            self.accounts_combo.addItem("— нет активных аккаунтов —", None)
            self.accounts_combo.setEnabled(False)
            self.logout_btn.setEnabled(False)
            self.remove_btn.setEnabled(False)
            self.account_info.setText(
                "Аккаунты не добавлены. Нажми «Войти в аккаунт», "
                "программа откроет браузер Яндекса."
            )
        else:
            self.accounts_combo.setEnabled(True)
            for a in logged_in:
                mark = "  ● активный" if a.is_active else ""
                self.accounts_combo.addItem(
                    f"{a.display_name} ({a.yandex_login}){mark}", a.id
                )
            self.logout_btn.setEnabled(True)
            self.remove_btn.setEnabled(True)

            # подсказка: как добавить ещё один аккаунт
            if len(logged_in) == 1:
                self.remove_btn.setToolTip(
                    "Можно добавить второй аккаунт: нажми «Войти в аккаунт» "
                    "и оставь галочку «Показать выбор аккаунта»"
                )

            # восстановим выбор
            if current is not None:
                idx = self.accounts_combo.findData(current)
                if idx >= 0:
                    self.accounts_combo.setCurrentIndex(idx)

        self.accounts_combo.blockSignals(False)
        self._on_account_changed()

    def _current_account_id(self) -> int | None:
        return self.accounts_combo.currentData()

    def _forget_account_cache(self, account_id: int) -> None:
        """
        Забывает клиент и размер Диска для аккаунта.

        Обязательно после входа заново: клиент держит старый токен внутри
        сессии requests, и без сброса запросы ушли бы с просроченным токеном.
        """
        self.clients.pop(account_id, None)
        self.space_cache.pop(account_id, None)

    def _on_account_changed(self) -> None:
        acc_id = self._current_account_id()
        if acc_id is None:
            self.account_info.setText("Аккаунт не выбран.")
            return

        acc = self.db.get_account(acc_id)
        if not acc:
            return

        # делаем выбранный аккаунт активным в базе
        if not acc.is_active:
            self.db.set_active_account(acc_id)
            self.refresh_accounts()
            return

        self.account_info.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px;"
        )
        cached = self.space_cache.get(acc_id)
        if cached:
            used, total = cached
            self.account_info.setText(
                f"{acc.display_name} · {acc.yandex_login}\n"
                f"Занято {human_size(used)} из {human_size(total)}"
            )
        else:
            self.account_info.setText(
                f"{acc.display_name} · {acc.yandex_login}\n"
                f"Запрашиваю свободное место…"
            )
            self._load_disk_info(acc_id, acc.access_token)

    def _load_disk_info(self, account_id: int, token: str) -> None:
        self._info_worker = DiskInfoWorker(token, account_id)
        self._info_worker.done.connect(self._on_disk_info)
        self._info_worker.failed.connect(self._on_disk_info_failed)
        self._info_worker.start()

    def _on_disk_info(self, login: str, used: int, total: int,
                      name: str) -> None:
        acc_id = self._current_account_id()
        if acc_id is None:
            return
        self.space_cache[acc_id] = (used, total)
        free = max(total - used, 0)
        self.account_info.setText(
            f"{name} · {login}\n"
            f"Занято {human_size(used)} из {human_size(total)} · "
            f"свободно {human_size(free)}"
        )

    def _on_disk_info_failed(self, msg: str) -> None:
        try:
            acc_id, err = msg.split("|", 1)
        except ValueError:
            acc_id, err = "", msg
        self.account_info.setStyleSheet(
            f"color: {theme.WARNING}; font-size: 11px;"
        )
        self.account_info.setText(f"Не удалось узнать место: {err}")

    def _on_login(self) -> None:
        """Вход в Яндекс.Диск. Можно добавить несколько разных аккаунтов."""
        # подсказываем диалогу, какие аккаунты уже известны и кто был последним
        accounts = self.db.list_accounts()
        known = [a.yandex_login for a in accounts if a.yandex_login]
        active = self.db.get_active_account()
        last = active.yandex_login if active else (known[-1] if known else None)

        dlg = LoginDialog(self, known_accounts=known, last_login=last)
        self._login_dialog = dlg
        if dlg.exec() != QDialog.Accepted or not dlg.login_data:
            return

        login, name, access, refresh, expires = dlg.login_data

        # определяем, это новый аккаунт или повторный вход в существующий
        was_known = any(a.yandex_login == login for a in accounts)

        acc_id = self.db.add_account(login, name, access, refresh, expires)
        self.db.set_active_account(acc_id)

        # сбрасываем кэш: у записи мог остаться старый клиент с прежним токеном
        self._forget_account_cache(acc_id)
        self.refresh_accounts()

        if was_known:
            QMessageBox.information(
                self, "Вход выполнен",
                f"Вошёл в аккаунт «{name}» ({login}).\n\n"
                f"Чтобы добавить ещё один, нажми «Войти в аккаунт» "
                f"и оставь галочку выбора аккаунта."
            )
        else:
            QMessageBox.information(
                self, "Аккаунт добавлен",
                f"Аккаунт «{name}» ({login}) добавлен и сделан активным.\n\n"
                f"Всего аккаунтов: {len(self.db.list_accounts())}."
            )

    def _on_logout(self) -> None:
        acc_id = self._current_account_id()
        if acc_id is None:
            return
        acc = self.db.get_account(acc_id)
        if not acc:
            return

        ans = QMessageBox.question(
            self, "Выход из аккаунта",
            f"Выйти из «{acc.display_name}»?\n\n"
            f"История загрузок останется, удалится только доступ.\n"
            f"Чтобы снова грузить, придётся войти заново.",
        )
        if ans != QMessageBox.Yes:
            return

        self.db.logout_account(acc_id)
        self._forget_account_cache(acc_id)   # стираем и клиент, и размер
        self.refresh_accounts()
        QMessageBox.information(
            self, "Выход выполнен",
            "Доступ удалён. История загрузок сохранена."
        )

    def _on_remove(self) -> None:
        acc_id = self._current_account_id()
        if acc_id is None:
            return
        acc = self.db.get_account(acc_id)
        if not acc:
            return

        ans = QMessageBox.question(
            self, "Удалить аккаунт",
            f"Удалить «{acc.display_name}» ({acc.yandex_login}) "
            f"из программы?\n\n"
            f"История его загрузок останется в базе.",
        )
        if ans != QMessageBox.Yes:
            return

        self.db.remove_account(acc_id)
        self.clients.pop(acc_id, None)
        self.space_cache.pop(acc_id, None)
        self.refresh_accounts()
        self.refresh_history()

    # ---------- история ----------

    def refresh_history(self) -> None:
        mode = self.filter_combo.currentData()
        records = self.db.list_uploads()

        if mode == STATE_UPLOADED:
            records = [r for r in records if r.upload_state == STATE_UPLOADED]
        elif mode == STATE_FAILED:
            records = [r for r in records if r.upload_state == STATE_FAILED]

        self.table.setRowCount(len(records))

        for row, r in enumerate(records):
            interval = (f"{r.rec_start} — {r.rec_end[11:]}"
                        if r.rec_start and r.rec_end else "—")

            cells = [
                r.channel_name or f"канал {r.channel}",
                interval,
                human_size(r.local_size or r.cloud_size),
                STATE_LABELS.get(r.upload_state, r.upload_state),
                r.account_login or "—",
                (r.uploaded_at or "—")[:16],
            ]

            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col == 3:
                    color = STATE_COLORS.get(r.upload_state, theme.TEXT)
                    item.setForeground(QColor(color))
                if r.upload_state == STATE_FAILED and r.upload_error and col == 3:
                    item.setToolTip(r.upload_error)
                self.table.setItem(row, col, item)

        if not records:
            self.history_info.setText("Записей нет.")
        else:
            total = sum((r.local_size or r.cloud_size) for r in records)
            self.history_info.setText(
                f"Записей: {len(records)} · общий объём: {human_size(total)}"
            )

    def refresh_all(self) -> None:
        self.refresh_accounts()
        self.refresh_history()

    # ---------- для вкладки выгрузки ----------

    def get_active_client(self) -> tuple[int | None, YaDiskClient | None]:
        """
        Возвращает (account_id, client) активного аккаунта.
        Клиент кэшируется, чтобы не создавать сессию на каждую загрузку.
        """
        acc = self.db.get_active_account()
        if not acc or not acc.access_token:
            return None, None

        client = self.clients.get(acc.id)
        if client is None:
            client = YaDiskClient(acc.access_token)
            self.clients[acc.id] = client
        return acc.id, client

    def invalidate_client(self, account_id: int) -> None:
        self.clients.pop(account_id, None)
