"""Проверки входа в Яндекс: сборка ссылки и выбор аккаунта."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from dahua_exporter.oauth import (
    AUTH_URL,
    CLIENT_ID,
    REDIRECT_URI,
    build_auth_url,
)


def test_pkce_challenge_is_sha256_base64url():
    verifier = "test-verifier-123"
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert len(expected) == 43
    assert all(c.isalnum() or c in "-_" for c in expected)


def _params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_auth_url_has_required_params():
    url = build_auth_url("STATE123", "CHALLENGE456")
    assert url.startswith(AUTH_URL)

    p = _params(url)
    assert p["client_id"] == CLIENT_ID
    assert p["redirect_uri"] == REDIRECT_URI
    assert p["response_type"] == "code"
    assert p["state"] == "STATE123"
    assert p["code_challenge"] == "CHALLENGE456"
    assert p["code_challenge_method"] == "S256"


def test_auth_url_omits_force_confirm_by_default():
    """По умолчанию параметра нет — Яндекс вернёт текущий аккаунт молча."""
    assert "force_confirm" not in _params(build_auth_url("S", "C"))


def test_force_confirm_asks_yandex_to_show_account_picker():
    """
    Ключевой параметр для смены аккаунта: с force_confirm=yes Яндекс
    обязан спросить разрешение и показать выбор аккаунта, даже если
    в браузере уже открыт другой Яндекс ID.
    """
    p = _params(build_auth_url("S", "C", force_confirm=True))
    assert p["force_confirm"] == "yes"


def test_login_hint_is_sent_and_escaped():
    """Подсказка логина передаётся корректно, включая символ @."""
    url = build_auth_url("S", "C", force_confirm=True,
                         login_hint="user@yandex.ru")
    assert "login_hint=user%40yandex.ru" in url
    assert _params(url)["login_hint"] == "user@yandex.ru"


def test_login_hint_absent_when_not_given():
    assert "login_hint" not in _params(build_auth_url("S", "C"))


def test_state_and_challenge_differ_between_logins():
    """Каждый вход должен иметь свои state и challenge."""
    a = _params(build_auth_url("S1", "C1"))
    b = _params(build_auth_url("S2", "C2"))
    assert a["state"] != b["state"]
    assert a["code_challenge"] != b["code_challenge"]
