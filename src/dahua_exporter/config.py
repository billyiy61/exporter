"""
Хранение настроек подключения.

Пароль НЕ пишется в открытом виде в файл настроек.
Он уходит в системное хранилище учётных данных через keyring
(на Windows — «Диспетчер учётных данных», шифруется ОС и привязан
к учётной записи пользователя).

Если keyring недоступен, приложение работает, но пароль придётся
вводить каждый запуск — это осознанный компромисс в пользу безопасности.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

# --- где лежат настройки --------------------------------------------------
# Windows: C:\Users\<ты>\.dahua_exporter\config.json
# Linux/macOS: ~/.dahua_exporter/config.json
CONFIG_DIR = Path.home() / ".dahua_exporter"
CONFIG_FILE = CONFIG_DIR / "config.json"

# --- имя сервиса в хранилище паролей -------------------------------------
KEYRING_SERVICE = "dahua_exporter"


@dataclass
class AppConfig:
    """Постоянные настройки. Пароля здесь нет — он в keyring."""

    host: str = "192.168.1.108"
    port: int = 80
    user: str = "admin"

    # режимы по умолчанию
    subtype: int = 0            # 0 = основной поток, 1 = субпоток
    video_codec: str = "h264"   # copy | h264
    audio_mode: str = "keep"    # strip_full | strip_left | strip_right | keep

    # куда сохранять выгрузки по умолчанию
    last_out_dir: str = ""

    # загрузка в Яндекс.Диск
    cloud_enabled: bool = False
    cloud_folder: str = "/DahuaExporter"

    # запоминать ли пароль (может быть выключено пользователем)
    remember_password: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def load_config() -> AppConfig:
    """Читает настройки. При любой проблеме возвращает значения по умолчанию."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return AppConfig()

    cfg = AppConfig()
    for key, val in data.items():
        if hasattr(cfg, key):
            setattr(cfg, key, val)
    return cfg


def save_config(cfg: AppConfig) -> None:
    """Пишет настройки на диск. Пароль сюда НЕ попадает."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(cfg.to_json(), encoding="utf-8")
        # на Unix ограничим права: только владелец
        if os.name != "nt":
            CONFIG_FILE.chmod(0o600)
    except OSError:
        # не критично: не смогли сохранить — просто не запомним настройки
        pass


# --- пароль ---------------------------------------------------------------

def get_password(user: str) -> str | None:
    """Пробует достать пароль из системного хранилища."""
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, user)
    except Exception:  # noqa: BLE001
        return None


def set_password(user: str, password: str) -> bool:
    """
    Сохраняет пароль в системное хранилище.
    Возвращает True при успехе, False если keyring недоступен.
    """
    try:
        import keyring
    except ImportError:
        return False
    try:
        if password:
            keyring.set_password(KEYRING_SERVICE, user, password)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, user)
            except Exception:  # noqa: BLE001
                pass
        return True
    except Exception:  # noqa: BLE001
        return False


def forget_password(user: str) -> None:
    """Удаляет сохранённый пароль."""
    set_password(user, "")


def keyring_available() -> bool:
    """Доступно ли системное хранилище паролей."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except ImportError:
        return False
    try:
        backend = keyring.get_keyring()
        return not isinstance(backend, FailKeyring)
    except Exception:  # noqa: BLE001
        return False
