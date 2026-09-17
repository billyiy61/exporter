"""
SQLite-хранилище: аккаунты Яндекс.Диска и история загрузок.

Зачем база, а не JSON:
  * нужно хранить связи «аккаунт → загрузки»
  * нужны быстрые выборки «что готово к удалению локально»
  * данные должны переживать перезапуск и не биться при сбое

Токены лежат в таблице accounts. Файл базы — отдельно от конфига,
чтобы его можно было удалить, не потеряв настройки подключения к NVR.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DB_DIR = Path.home() / ".dahua_exporter"
DB_FILE = DB_DIR / "history.db"

# --- состояния загрузки ----------------------------------------------------

STATE_PENDING = "pending"       # в очереди, ещё не начинали
STATE_DOWNLOADING = "downloading"  # качается с NVR
STATE_UPLOADING = "uploading"   # заливается в облако
STATE_UPLOADED = "uploaded"     # успешно залито
STATE_FAILED = "failed"         # ошибка


@dataclass
class Account:
    id: int
    yandex_login: str
    display_name: str
    access_token: str
    refresh_token: str
    expires_at: str | None
    is_active: bool
    added_at: str | None = None
    last_used_at: str | None = None


@dataclass
class UploadRecord:
    id: int
    account_id: int
    device_host: str
    channel: int
    channel_name: str
    rec_start: str
    rec_end: str
    local_path: str
    local_size: int
    local_exists: bool
    cloud_path: str
    cloud_size: int
    upload_state: str
    upload_error: str | None
    created_at: str | None
    uploaded_at: str | None
    confirmed_at: str | None
    deleted_local: bool
    account_login: str = ""   # подтягивается JOIN-ом


class Database:
    """Потокобезопасная обёртка над SQLite."""

    def __init__(self, path: Path | str = DB_FILE):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    # ---------- схема ----------

    def _create_schema(self) -> None:
        with self._lock:
            c = self._conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    yandex_login  TEXT UNIQUE NOT NULL,
                    display_name  TEXT,
                    access_token  TEXT,
                    refresh_token TEXT,
                    expires_at    TEXT,
                    is_active     INTEGER DEFAULT 0,
                    added_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_used_at  TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS uploads (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id    INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                    device_host   TEXT,
                    channel       INTEGER,
                    channel_name  TEXT,
                    rec_start     TEXT,
                    rec_end       TEXT,
                    local_path    TEXT,
                    local_size    INTEGER,
                    local_exists  INTEGER DEFAULT 1,
                    cloud_path    TEXT,
                    cloud_size    INTEGER,
                    upload_state  TEXT DEFAULT 'pending',
                    upload_error  TEXT,
                    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                    uploaded_at   TEXT,
                    confirmed_at  TEXT,
                    deleted_local INTEGER DEFAULT 0
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_uploads_state
                ON uploads(upload_state, confirmed_at, deleted_local)
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_uploads_account
                ON uploads(account_id)
            """)
            self._conn.commit()

    # ---------- аккаунты ----------

    def add_account(self, login: str, display_name: str,
                    access_token: str, refresh_token: str,
                    expires_in: int | None = None) -> int:
        expires_at = None
        if expires_in:
            expires_at = (datetime.now() +
                          timedelta(seconds=expires_in)).strftime(
                              "%Y-%m-%d %H:%M:%S")
        with self._lock:
            c = self._conn.cursor()
            c.execute("""
                INSERT INTO accounts
                    (yandex_login, display_name, access_token,
                     refresh_token, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(yandex_login) DO UPDATE SET
                    display_name  = excluded.display_name,
                    access_token  = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at    = excluded.expires_at,
                    added_at      = CURRENT_TIMESTAMP
            """, (login, display_name, access_token, refresh_token, expires_at))
            self._conn.commit()
            row = c.execute("SELECT id FROM accounts WHERE yandex_login = ?",
                            (login,)).fetchone()
            return row["id"]

    def list_accounts(self) -> list[Account]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM accounts ORDER BY is_active DESC, added_at"
            ).fetchall()
        return [self._row_to_account(r) for r in rows]

    def get_account(self, account_id: int) -> Account | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM accounts WHERE id = ?",
                                   (account_id,)).fetchone()
        return self._row_to_account(r) if r else None

    def get_active_account(self) -> Account | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM accounts WHERE is_active = 1 LIMIT 1"
            ).fetchone()
        return self._row_to_account(r) if r else None

    def set_active_account(self, account_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE accounts SET is_active = 0")
            self._conn.execute(
                "UPDATE accounts SET is_active = 1, last_used_at = CURRENT_TIMESTAMP "
                "WHERE id = ?", (account_id,))
            self._conn.commit()

    def logout_account(self, account_id: int) -> None:
        """
        Выход из аккаунта: стираем токены, но НЕ историю загрузок.
        Именно это просил пользователь — история должна остаться.
        """
        with self._lock:
            self._conn.execute("""
                UPDATE accounts
                SET access_token = '', refresh_token = '',
                    expires_at = NULL, is_active = 0
                WHERE id = ?
            """, (account_id,))
            self._conn.commit()

    def remove_account(self, account_id: int) -> None:
        """Полное удаление аккаунта. История остаётся (account_id -> NULL)."""
        with self._lock:
            self._conn.execute("DELETE FROM accounts WHERE id = ?",
                               (account_id,))
            self._conn.commit()

    # ---------- загрузки ----------

    def add_upload(self, account_id: int | None, device_host: str,
                   channel: int, channel_name: str,
                   rec_start: str, rec_end: str,
                   local_path: str = "", local_size: int = 0,
                   cloud_path: str = "") -> int:
        with self._lock:
            c = self._conn.cursor()
            c.execute("""
                INSERT INTO uploads
                    (account_id, device_host, channel, channel_name,
                     rec_start, rec_end, local_path, local_size,
                     cloud_path, upload_state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (account_id, device_host, channel, channel_name,
                  rec_start, rec_end, local_path, local_size,
                  cloud_path, STATE_PENDING))
            self._conn.commit()
            return c.lastrowid

    def update_upload(self, upload_id: int, **fields) -> None:
        if not fields:
            return
        allowed = {
            "local_path", "local_size", "local_exists",
            "cloud_path", "cloud_size", "upload_state",
            "upload_error", "uploaded_at", "confirmed_at",
            "deleted_local",
        }
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [upload_id]
        with self._lock:
            self._conn.execute(f"UPDATE uploads SET {sets} WHERE id = ?", vals)
            self._conn.commit()

    def list_uploads(self, limit: int = 200) -> list[UploadRecord]:
        with self._lock:
            rows = self._conn.execute("""
                SELECT u.*, COALESCE(a.yandex_login, '—') AS account_login
                FROM uploads u
                LEFT JOIN accounts a ON a.id = u.account_id
                ORDER BY u.id DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [self._row_to_upload(r) for r in rows]

    def list_pending_cleanup(self) -> list[UploadRecord]:
        """
        Записи, которые успешно залиты в облако, но локальная копия ещё
        лежит и не подтверждена к удалению. Это содержимое вкладки
        «Ожидает подтверждения».
        """
        with self._lock:
            rows = self._conn.execute("""
                SELECT u.*, COALESCE(a.yandex_login, '—') AS account_login
                FROM uploads u
                LEFT JOIN accounts a ON a.id = u.account_id
                WHERE u.upload_state = 'uploaded'
                  AND u.deleted_local = 0
                  AND u.confirmed_at IS NULL
                ORDER BY u.uploaded_at DESC
            """).fetchall()
        return [self._row_to_upload(r) for r in rows]

    # ---------- преобразование строк ----------

    @staticmethod
    def _row_to_account(r: sqlite3.Row) -> Account:
        return Account(
            id=r["id"],
            yandex_login=r["yandex_login"],
            display_name=r["display_name"] or r["yandex_login"],
            access_token=r["access_token"] or "",
            refresh_token=r["refresh_token"] or "",
            expires_at=r["expires_at"],
            is_active=bool(r["is_active"]),
            added_at=r["added_at"],
            last_used_at=r["last_used_at"],
        )

    @staticmethod
    def _row_to_upload(r: sqlite3.Row) -> UploadRecord:
        return UploadRecord(
            id=r["id"],
            account_id=r["account_id"],
            device_host=r["device_host"] or "",
            channel=r["channel"] or 0,
            channel_name=r["channel_name"] or "",
            rec_start=r["rec_start"] or "",
            rec_end=r["rec_end"] or "",
            local_path=r["local_path"] or "",
            local_size=r["local_size"] or 0,
            local_exists=bool(r["local_exists"]),
            cloud_path=r["cloud_path"] or "",
            cloud_size=r["cloud_size"] or 0,
            upload_state=r["upload_state"] or STATE_PENDING,
            upload_error=r["upload_error"],
            created_at=r["created_at"],
            uploaded_at=r["uploaded_at"],
            confirmed_at=r["confirmed_at"],
            deleted_local=bool(r["deleted_local"]),
            account_login=r["account_login"] if "account_login" in r.keys() else "",
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
