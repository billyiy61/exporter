"""Проверки входа в несколько аккаунтов (логика базы и окна входа)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _db(tmp_path):
    from dahua_exporter.database import Database
    return Database(tmp_path / "h.db")


def test_soft_logout_keeps_row_and_allows_relogin(tmp_path):
    """
    Мягкий выход стирает доступ, но запись остаётся.
    Повторный вход тем же логином обновляет её, а не плодит дубли.
    """
    db = _db(tmp_path)
    first = db.add_account("a@yandex.ru", "A", "tok", "ref", 3600)
    db.set_active_account(first)

    db.logout_account(first)
    acc = db.get_account(first)
    assert acc.access_token == "", "токен должен быть стёрт"
    assert acc.refresh_token == ""
    assert acc.is_active is False

    again = db.add_account("a@yandex.ru", "A", "tok2", "ref2", 3600)
    assert again == first, "повторный вход не должен создавать вторую запись"
    assert len(db.list_accounts()) == 1


def test_different_login_creates_second_account(tmp_path):
    """Вход под другим логином добавляет отдельный аккаунт."""
    db = _db(tmp_path)
    a1 = db.add_account("pervyi@yandex.ru", "Первый", "t1", "r1", 3600)
    db.set_active_account(a1)

    a2 = db.add_account("vtoroy@yandex.ru", "Второй", "t2", "r2", 3600)
    assert a2 != a1

    accounts = db.list_accounts()
    assert len(accounts) == 2
    assert {x.yandex_login for x in accounts} == {
        "pervyi@yandex.ru", "vtoroy@yandex.ru"
    }


def test_switching_active_account(tmp_path):
    """Переключение между двумя вошедшими аккаунтами работает."""
    db = _db(tmp_path)
    a1 = db.add_account("one@yandex.ru", "Первый", "t1", "r1", 3600)
    a2 = db.add_account("two@yandex.ru", "Второй", "t2", "r2", 3600)

    db.set_active_account(a1)
    assert db.get_active_account().yandex_login == "one@yandex.ru"
    assert db.get_account(a2).is_active is False

    db.set_active_account(a2)
    assert db.get_active_account().yandex_login == "two@yandex.ru"
    assert db.get_account(a1).is_active is False

    # активным может быть ровно один
    assert sum(1 for a in db.list_accounts() if a.is_active) == 1


def test_active_account_is_none_after_logout(tmp_path):
    """После мягкого выхода активного аккаунта нет, пока не войдёшь снова."""
    db = _db(tmp_path)
    a1 = db.add_account("one@yandex.ru", "Первый", "t1", "r1", 3600)
    db.set_active_account(a1)
    db.logout_account(a1)
    assert db.get_active_account() is None


def test_login_dialog_does_not_open_browser_on_init(tmp_path):
    """
    Регрессия: раньше браузер открывался прямо при показе окна,
    и выбрать аккаунт было нельзя.
    """
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from dahua_exporter.cloud_dialogs import LoginDialog

    app = QApplication.instance() or QApplication([])
    dlg = LoginDialog(known_accounts=["a@yandex.ru"], last_login="a@yandex.ru")

    assert dlg.worker is None, "поток входа не должен стартовать сам"
    assert dlg.login_data is None
    assert dlg.choose_cb.isChecked(), "галочка выбора аккаунта включена по умолчанию"
    assert dlg.login_btn.isEnabled()
    dlg.close()


def test_login_dialog_passes_force_confirm(monkeypatch, tmp_path):
    """Галочка превращается в force_confirm — иначе аккаунт не сменить."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from dahua_exporter import cloud_dialogs

    captured = {}

    class _Sig:
        """Заглушка сигнала: позволяет подключиться, но никуда не шлёт."""

        def connect(self, *args, **kwargs):
            pass

    class FakeWorker:
        done = _Sig()
        failed = _Sig()

        def __init__(self, force_confirm=False, login_hint=None):
            captured["force_confirm"] = force_confirm
            captured["login_hint"] = login_hint

        def start(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr(cloud_dialogs, "LoginWorker", FakeWorker)

    app = QApplication.instance() or QApplication([])
    dlg = cloud_dialogs.LoginDialog(known_accounts=["a@yandex.ru"],
                                    last_login="a@yandex.ru")
    dlg.choose_cb.setChecked(True)
    dlg._start_login()

    assert captured["force_confirm"] is True
    assert captured["login_hint"] is None, \
        "при выборе аккаунта подсказка логина не нужна"
    dlg.close()


def test_unchecking_forces_hint_to_last_account(monkeypatch):
    """Без галочки подставляем прежний логин для быстрого повторного входа."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from dahua_exporter import cloud_dialogs

    captured = {}

    class _Sig:
        """Заглушка сигнала: позволяет подключиться, но никуда не шлёт."""

        def connect(self, *args, **kwargs):
            pass

    class FakeWorker:
        done = _Sig()
        failed = _Sig()

        def __init__(self, force_confirm=False, login_hint=None):
            captured["force_confirm"] = force_confirm
            captured["login_hint"] = login_hint

        def start(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr(cloud_dialogs, "LoginWorker", FakeWorker)

    app = QApplication.instance() or QApplication([])
    dlg = cloud_dialogs.LoginDialog(last_login="a@yandex.ru")
    dlg.choose_cb.setChecked(False)
    dlg._start_login()

    assert captured["force_confirm"] is False
    assert captured["login_hint"] == "a@yandex.ru"
    dlg.close()
