"""
Вход в Яндекс через OAuth 2.0 (Authorization Code + PKCE).

Как это работает:
  1. Программа генерирует code_verifier и code_challenge (PKCE, RFC 7636).
     Это нужно, потому что у нас публичный клиент без client_secret —
     секрет негде хранить безопасно, поэтому подтверждаем себя иначе.
  2. Открывается браузер со ссылкой на oauth.yandex.ru.
  3. Пользователь входит и подтверждает доступ.
  4. Яндекс перенаправляет браузер на http://localhost:8080/callback?code=...
  5. Наш локальный сервер (поднят на время входа) ловит код.
  6. Код вместе с code_verifier меняется на access_token + refresh_token.

Никакие пароли через программу не проходят — только токены.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

# --- константы приложения --------------------------------------------------

CLIENT_ID = "b7b86e8b845f474e8a7d2fb54facbf5b"
REDIRECT_PORT = 8080
REDIRECT_PATH = "/callback"
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}{REDIRECT_PATH}"

AUTH_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
INFO_URL = "https://login.yandex.ru/info"

SCOPES = "cloud_api:disk.read cloud_api:disk.write cloud_api:disk.info"


class OAuthError(Exception):
    """Ошибка при входе в Яндекс."""


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"


# --- PKCE ------------------------------------------------------------------

def _make_pkce() -> tuple[str, str]:
    """Возвращает (code_verifier, code_challenge)."""
    verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# --- ловля callback --------------------------------------------------------

class _CallbackHandler(BaseHTTPRequestHandler):
    """Принимает ОДИН запрос от браузера и сохраняет код."""

    result: dict = {}

    def log_message(self, *args):    # глушим шум в консоли
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != REDIRECT_PATH:
            self._respond(404, "Не тот адрес",
                          "Ожидался /callback.")
            return

        q = parse_qs(parsed.query)

        if "error" in q:
            _CallbackHandler.result = {"error": q["error"][0]}
            self._respond(
                200, "Вход отменён",
                "Яндекс вернул ошибку. Закрой окно и попробуй снова.",
            )
        elif "code" in q:
            _CallbackHandler.result = {
                "code": q["code"][0],
                "state": q.get("state", [""])[0],
            }
            self._respond(
                200, "Вход выполнен",
                "Можно закрыть это окно и вернуться в программу.",
            )
        else:
            _CallbackHandler.result = {"error": "no_code"}
            self._respond(400, "Нет кода", "Яндекс не передал код.")

        # гасим сервер после первого же запроса
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _respond(self, code: int, title: str, text: str) -> None:
        body = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>{title}</title></head>
<body style="margin:0;font-family:'Segoe UI',sans-serif;
             background:#0f1419;color:#e6edf3;
             display:flex;align-items:center;justify-content:center;
             height:100vh">
  <div style="text-align:center;background:#161b22;
              border:1px solid #2a3544;border-radius:14px;
              padding:48px 64px;max-width:460px">
    <h1 style="margin:0 0 14px;font-size:22px;color:#2f81f7">{title}</h1>
    <p style="margin:0;color:#8b98a8;font-size:15px;line-height:1.5">{text}</p>
  </div>
</body></html>"""
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# --- публичные функции -----------------------------------------------------

def build_auth_url(state: str, challenge: str,
                   force_confirm: bool = False,
                   login_hint: str | None = None) -> str:
    """
    Собирает ссылку на страницу входа Яндекса.

    force_confirm=True — Яндекс ОБЯЗАТЕЛЬНО спросит разрешение и покажет
    выбор аккаунта, даже если в браузере уже открыт чужой/старый Яндекс ID.
    Без этого параметра браузер молча выдаёт код для текущего аккаунта,
    и выбрать другой невозможно.

    login_hint — подсказка, какой логин подставить в форму (пользователь
    всё равно может его изменить).
    """
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if force_confirm:
        params["force_confirm"] = "yes"
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def authorize(open_browser: bool = True,
              timeout: int = 180,
              force_confirm: bool = False,
              login_hint: str | None = None) -> TokenBundle:
    """
    Полный цикл входа. Блокирует поток до завершения или таймаута.

    Вызывать из фонового потока, а не из GUI-потока —
    иначе окно замёрзнет на время ожидания.
    """
    verifier, challenge = _make_pkce()
    state = secrets.token_urlsafe(16)
    url = build_auth_url(state, challenge,
                         force_confirm=force_confirm,
                         login_hint=login_hint)

    # поднимаем сервер на порту 8080
    try:
        server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
    except OSError as e:
        raise OAuthError(
            f"Не удалось занять порт {REDIRECT_PORT}. "
            f"Возможно, он занят другой программой. ({e})"
        ) from e

    _CallbackHandler.result = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    if open_browser:
        webbrowser.open(url)

    # ждём, пока браузер придёт с кодом
    thread.join(timeout=timeout)
    server.server_close()

    result = _CallbackHandler.result
    if not result:
        raise OAuthError(
            "Время ожидания входа истекло. Попробуй снова."
        )
    if "error" in result:
        raise OAuthError(f"Яндекс вернул ошибку: {result['error']}")
    if result.get("state") != state:
        raise OAuthError(
            "Проверка state не прошла — возможная подмена запроса. "
            "Начни вход заново."
        )

    return _exchange_code(result["code"], verifier)


def _exchange_code(code: str, verifier: str) -> TokenBundle:
    """Меняет код на токены."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    }
    try:
        r = requests.post(TOKEN_URL, data=data, timeout=20)
    except requests.RequestException as e:
        raise OAuthError(f"Не удалось связаться с Яндексом: {e}") from e

    if r.status_code != 200:
        raise OAuthError(
            f"Яндекс отказал в выдаче токена: HTTP {r.status_code}. "
            f"{r.text[:300]}"
        )

    j = r.json()
    if "access_token" not in j:
        raise OAuthError(f"В ответе нет access_token: {j}")

    return TokenBundle(
        access_token=j["access_token"],
        refresh_token=j.get("refresh_token", ""),
        expires_in=int(j.get("expires_in", 31536000)),
        token_type=j.get("token_type", "bearer"),
    )


def refresh(refresh_token: str) -> TokenBundle:
    """Обновляет access_token по refresh_token."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    }
    try:
        r = requests.post(TOKEN_URL, data=data, timeout=20)
    except requests.RequestException as e:
        raise OAuthError(f"Не удалось обновить токен: {e}") from e

    if r.status_code != 200:
        raise OAuthError(
            "Не удалось обновить токен — возможно, доступ отозван. "
            "Войди заново."
        )

    j = r.json()
    return TokenBundle(
        access_token=j["access_token"],
        refresh_token=j.get("refresh_token", refresh_token),
        expires_in=int(j.get("expires_in", 31536000)),
        token_type=j.get("token_type", "bearer"),
    )


def get_user_info(access_token: str) -> dict:
    """Узнаёт логин и имя владельца токена."""
    r = requests.get(
        INFO_URL,
        params={"format": "json", "oauth_token": access_token},
        timeout=15,
    )
    if r.status_code != 200:
        raise OAuthError(f"Не удалось получить данные пользователя: "
                         f"HTTP {r.status_code}")
    return r.json()


def get_display_name(access_token: str) -> tuple[str, str]:
    """
    Возвращает (логин, отображаемое_имя) для сохранения в базе.
    При неудаче возвращает заглушки, а не падает.
    """
    try:
        info = get_user_info(access_token)
        login = info.get("login") or info.get("default_email") or "unknown"
        name = (info.get("real_name")
                or info.get("display_name")
                or login)
        return login, name
    except Exception:   # noqa: BLE001
        return "unknown", "Яндекс-аккаунт"
