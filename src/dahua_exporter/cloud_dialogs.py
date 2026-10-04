"""
Диалоги и виджеты для работы с Яндекс.Диском.

Здесь:
  * окно входа в аккаунт (запускает OAuth в фоне)
  * браузер папок Яндекс.Диска (выбор куда грузить)

Оба — модальные диалоги, вызываются из GUI.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from . import theme
from .oauth import OAuthError, authorize
from .yandex_disk import DiskItem, YaDiskError, YaDiskClient


# --------------------------------------------------------------------------
# Поток входа в аккаунт
# --------------------------------------------------------------------------

class LoginWorker(QThread):
    """Запускает OAuth в фоне, чтобы окно не замёрзло на время входа."""

    done = Signal(str, str, str, str, int)   # login, name, access, refresh, expires
    failed = Signal(str)

    def __init__(self, force_confirm: bool = False,
                 login_hint: str | None = None, parent=None):
        super().__init__(parent)
        # force_confirm=True заставляет Яндекс показать выбор аккаунта,
        # иначе браузер молча отдаёт код для уже открытого аккаунта.
        self.force_confirm = force_confirm
        self.login_hint = login_hint

    def run(self) -> None:
        try:
            bundle = authorize(open_browser=True,
                               force_confirm=self.force_confirm,
                               login_hint=self.login_hint)
        except OAuthError as e:
            self.failed.emit(str(e))
            return
        except Exception as e:            # noqa: BLE001
            self.failed.emit(f"Неожиданная ошибка входа: {e}")
            return

        # узнаём, кто это
        from .oauth import get_display_name
        login, name = get_display_name(bundle.access_token)
        self.done.emit(login, name, bundle.access_token,
                       bundle.refresh_token, bundle.expires_in)


# --------------------------------------------------------------------------
# Диалог входа
# --------------------------------------------------------------------------

class LoginDialog(QDialog):
    """
    Окно входа.

    Браузер открывается ТОЛЬКО после нажатия кнопки — раньше он стартовал
    сразу при показе окна, и пользователь не успевал ничего выбрать.

    Галочка «Показать выбор аккаунта» включает force_confirm: Яндекс
    покажет экран выбора аккаунта, поэтому можно войти под другим логином,
    а не только под тем, что уже открыт в браузере.
    """

    def __init__(self, parent=None, known_accounts: list[str] | None = None,
                 last_login: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Вход в Яндекс")
        self.setModal(True)
        self.resize(460, 300)

        self.login_data: tuple | None = None
        self._known = known_accounts or []
        self._last_login = last_login

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)

        title = QLabel("Вход в Яндекс.Диск")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {theme.TEXT};"
        )
        lay.addWidget(title)

        hint = QLabel(
            "Нажми «Открыть браузер» — откроется страница Яндекса. "
            "Войди под нужным аккаунтом и разреши доступ к Диску.\n\n"
            "Окно можно не закрывать: программа поймает ответ сама."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        lay.addWidget(hint)

        # --- список известных аккаунтов (подсказка) ---
        if self._known:
            names = ", ".join(self._known[:3])
            more = f" и ещё {len(self._known) - 3}" if len(self._known) > 3 else ""
            known_lbl = QLabel(f"Уже добавлены: {names}{more}")
            known_lbl.setWordWrap(True)
            known_lbl.setStyleSheet(
                f"color: {theme.TEXT_DIM}; font-size: 11px;"
            )
            lay.addWidget(known_lbl)

        # --- галочка выбора аккаунта ---
        self.choose_cb = QCheckBox("Показать выбор аккаунта в браузере")
        self.choose_cb.setChecked(True)
        if self._last_login:
            self.choose_cb.setToolTip(
                f"Если снять галочку, Яндекс может сразу вернуть "
                f"аккаунт «{self._last_login}»"
            )
        else:
            self.choose_cb.setToolTip(
                "Оставь включённым, чтобы войти под другим аккаунтом"
            )
        lay.addWidget(self.choose_cb)

        self.status = QLabel("Готово к входу.")
        self.status.setWordWrap(True)
        self.status.setObjectName("status")
        lay.addWidget(self.status)

        lay.addStretch()

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch()

        self.login_btn = QPushButton("Открыть браузер")
        self.login_btn.setObjectName("primary")
        self.login_btn.clicked.connect(self._start_login)
        btns.addWidget(self.login_btn)

        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        btns.addWidget(self.cancel_btn)
        lay.addLayout(btns)

        self.worker: LoginWorker | None = None

    # ---------- запуск входа ----------

    def _start_login(self) -> None:
        if self.worker is not None:
            return                      # уже идёт — второй раз не запускаем

        self.login_btn.setEnabled(False)
        self.choose_cb.setEnabled(False)
        self.status.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self.status.setText("Ожидаю вход в браузере…")

        # если пользователь вошёл под своим логином — подставим его
        hint_login = None
        if not self.choose_cb.isChecked() and self._last_login:
            hint_login = self._last_login

        self.worker = LoginWorker(
            force_confirm=self.choose_cb.isChecked(),
            login_hint=hint_login,
        )
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_done(self, login, name, access, refresh, expires) -> None:
        self.login_data = (login, name, access, refresh, expires)
        self.accept()

    def _on_failed(self, msg: str) -> None:
        self.status.setStyleSheet(f"color: {theme.DANGER};")
        self.status.setText(f"Ошибка: {msg}")
        self.login_btn.setEnabled(True)
        self.choose_cb.setEnabled(True)
        self.cancel_btn.setText("Закрыть")

    def closeEvent(self, event) -> None:
        """Закрытие окна во время входа не должно оставлять поток висеть."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait(2000)
        super().closeEvent(event)


def _api_path(raw: str) -> str:
    """
    Приводит путь от API Яндекс.Диска к виду, который принимает API.

    Проблема: в ответах API пути приходят с префиксом ресурса —
    "disk:/Папка/Вложенная". Если такой путь отправить обратно в
    параметре path, Яндекс ответит 404, потому что рабочая форма —
    "/Папка/Вложенная".

    Поэтому префикс "disk:" (и любые другие вида "<ресурс>:") срезаем.
    """
    if not raw:
        return "/"
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


# --------------------------------------------------------------------------
# Браузер папок Яндекс.Диска
# --------------------------------------------------------------------------

class FolderPickerDialog(QDialog):
    """Ходит по папкам облака и даёт выбрать, куда грузить."""

    def __init__(self, client: YaDiskClient, start_path: str = "/",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выбор папки на Яндекс.Диске")
        self.setModal(True)
        self.resize(600, 520)

        self.client = client
        self.current_path = start_path or "/"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        # --- путь ---
        nav = QHBoxLayout()
        nav.setSpacing(8)

        self.up_btn = QPushButton("↑ Вверх")
        self.up_btn.setFixedWidth(96)
        self.up_btn.clicked.connect(self._go_up)
        nav.addWidget(self.up_btn)

        self.path_edit = QLineEdit(self.current_path)
        self.path_edit.returnPressed.connect(self._jump_to_path)
        nav.addWidget(self.path_edit, 1)

        self.go_btn = QPushButton("Перейти")
        self.go_btn.setFixedWidth(96)
        self.go_btn.clicked.connect(self._jump_to_path)
        nav.addWidget(self.go_btn)
        lay.addLayout(nav)

        # --- список ---
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._enter_item)
        lay.addWidget(self.list, 1)

        self.info = QLabel("…")
        self.info.setObjectName("hint")
        lay.addWidget(self.info)

        # --- кнопки ---
        row = QHBoxLayout()
        row.setSpacing(8)

        self.new_btn = QPushButton("Создать папку")
        self.new_btn.clicked.connect(self._create_folder)
        row.addWidget(self.new_btn)

        row.addStretch()

        self.choose_btn = QPushButton("Выбрать эту папку")
        self.choose_btn.setObjectName("primary")
        self.choose_btn.clicked.connect(self.accept)
        row.addWidget(self.choose_btn)

        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.cancel_btn)
        lay.addLayout(row)

        self._load()

    # ---------- загрузка содержимого ----------

    def _load(self) -> None:
        self.list.clear()
        self.path_edit.setText(self.current_path)
        self.up_btn.setEnabled(self.current_path not in ("", "/"))

        try:
            items = self.client.list_folder(self.current_path)
        except YaDiskError as e:
            self.info.setStyleSheet(f"color: {theme.DANGER}; font-size: 11px;")
            self.info.setText(f"Ошибка: {e}")
            return

        for it in items:
            if not it.is_dir:
                continue   # показываем только папки
            item = QListWidgetItem(f"📁  {it.name}")
            # Яндекс отдаёт путь с префиксом "disk:", например
            # "disk:/DahuaExporter". Для запросов нужен чистый путь
            # "/DahuaExporter". Без нормализации следующий поиск уходил
            # на "disk:/DahuaExporter" и падал с ошибкой 404.
            item.setData(Qt.UserRole, _api_path(it.path))
            self.list.addItem(item)

        self.info.setStyleSheet(f"color: {theme.TEXT_MUTE}; font-size: 11px;")
        if self.list.count() == 0:
            self.info.setText(
                "Вложенных папок нет. Можно создать или выбрать эту."
            )
        else:
            self.info.setText(
                f"Папок внутри: {self.list.count()}. "
                f"Двойной клик — войти, кнопка ниже — выбрать эту."
            )

    def _enter_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self.current_path = path
            self._load()

    def _go_up(self) -> None:
        if self.current_path in ("", "/"):
            return
        parent = self.current_path.rsplit("/", 1)[0] or "/"
        self.current_path = parent
        self._load()

    def _jump_to_path(self) -> None:
        p = self.path_edit.text().strip() or "/"
        if not p.startswith("/"):
            p = "/" + p
        self.current_path = p
        self._load()

    def _create_folder(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "Новая папка", "Имя папки:"
        )
        if not ok or not name.strip():
            return
        name = name.strip().strip("/")
        new_path = f"{self.current_path.rstrip('/')}/{name}"

        try:
            self.client.create_folder(new_path)
        except YaDiskError as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать: {e}")
            return

        self.current_path = new_path
        self._load()

    def selected_path(self) -> str:
        return self.current_path


# --------------------------------------------------------------------------
# Строка выбора папки (виджет для встраивания)
# --------------------------------------------------------------------------

class FolderSelector(QWidget):
    """
    Виджет «папка на Диске + кнопка Обзор».
    Используется на вкладке выгрузки.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client: YaDiskClient | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.edit = QLineEdit("/DahuaExporter")
        self.edit.setPlaceholderText("/DahuaExporter")
        lay.addWidget(self.edit, 1)

        self.browse_btn = QPushButton("Обзор…")
        self.browse_btn.setFixedWidth(100)
        self.browse_btn.setEnabled(False)
        self.browse_btn.clicked.connect(self._browse)
        lay.addWidget(self.browse_btn)

    def set_client(self, client: YaDiskClient | None) -> None:
        """Клиент появляется, когда пользователь вошёл в аккаунт."""
        self._client = client
        self.browse_btn.setEnabled(client is not None)
        if client is None:
            self.browse_btn.setToolTip("Сначала войди в аккаунт")
        else:
            self.browse_btn.setToolTip("")

    def _browse(self) -> None:
        if self._client is None:
            return
        start = self.edit.text().strip() or "/"
        dlg = FolderPickerDialog(self._client, start, self)
        if dlg.exec() == QDialog.Accepted:
            self.edit.setText(dlg.selected_path())

    def path(self) -> str:
        p = self.edit.text().strip() or "/DahuaExporter"
        if not p.startswith("/"):
            p = "/" + p
        return p
