"""
Тесты клиента Яндекс.Диска.

Свой мок-сервер, ПОВТОРЯЮЩИЙ поведение nginx: если в запросе приходят
одновременно Content-Length и Transfer-Encoding, отвечает 400 Bad Request
HTML-страницей. Именно так падала загрузка, когда тело отправлялось
генератором с ручным Content-Length.

Внешних файлов не требуется — тесты идут на чистой машине.
"""

from __future__ import annotations

import json
import socketserver
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler

import pytest

from dahua_exporter.yandex_disk import (
    UploadCancelled,
    YaDiskClient,
    YaDiskError,
)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---------- служебное ----------

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query
        )

    # ---------- запрос ссылки ----------

    def do_GET(self) -> None:
        if self.path.startswith("/v1/disk/resources/upload"):
            q = self._query()
            self.server.upload_link_query = q
            if "path" not in q:
                return self._json(400, {
                    "error": "FieldValidationError",
                    "description": 'Error validating field "path": '
                                   'This field is required.',
                    "message": 'Ошибка проверки поля "path": '
                               'Это поле является обязательным.',
                })
            port = self.server.server_address[1]
            return self._json(200, {
                "href": f"http://127.0.0.1:{port}/upload-target",
                "method": "PUT",
            })
        return self._json(404, {"error": "NotFoundError"})

    # ---------- ЗАГРУЗКА ПО ССЫЛКЕ (эта ручка требует url) ----------

    def do_POST(self) -> None:
        q = self._query()
        self.server.upload_link_query = q
        if "url" not in q:
            return self._json(400, {
                "error": "FieldValidationError",
                "description": 'Error validating field "url": '
                               'This field is required.',
                "message": 'Ошибка проверки поля "url": '
                           'Это поле является обязательным.',
            })
        return self._json(200, {"href": "http://x/y", "method": "PUT"})

    # ---------- приём файла ----------

    def do_PUT(self) -> None:
        if self.path.startswith("/v1/disk/resources"):
            # создание папки
            self.server.created_folders.append(self._query().get("path", [""])[0])
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if self.path != "/upload-target":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        headers = {k.lower(): v for k, v in self.headers.items()}
        self.server.last_upload_headers = headers

        content_length = headers.get("content-length")
        transfer = headers.get("transfer-encoding")

        # ГЛАВНАЯ ПРОВЕРКА: оба заголовка = недопустимо, nginx даёт 400
        if content_length and transfer:
            html = (b"<html><head><title>400 Bad Request</title></head>"
                    b"<body><center><h1>400 Bad Request</h1></center>"
                    b"<hr><center>nginx/1.18.0</center></body></html>")
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        received = 0
        if content_length:
            want = int(content_length)
            while received < want:
                piece = self.rfile.read(min(65536, want - received))
                if not piece:
                    break
                received += len(piece)

        self.server.received_bytes = received
        self.server.upload_count += 1
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def mock_api(monkeypatch):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.received_bytes = 0
    server.upload_count = 0
    server.upload_link_query = {}
    server.last_upload_headers = {}
    server.created_folders = []

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    import dahua_exporter.yandex_disk as module
    monkeypatch.setattr(
        module, "API_BASE",
        f"http://127.0.0.1:{server.server_address[1]}/v1/disk",
    )
    yield server
    server.shutdown()
    server.server_close()


# ---------------- загрузка ----------------

def test_upload_sends_correct_headers(mock_api, tmp_path):
    """
    РЕГРЕССИЯ на ошибку 400: в запросе должен быть ТОЛЬКО Content-Length.
    Если рядом появится Transfer-Encoding: chunked, сервер ответит 400.
    """
    source = tmp_path / "video.mp4"
    source.write_bytes(b"x" * (1024 * 1024 + 7))

    YaDiskClient("t").upload_file(str(source), "/Archive/video.mp4")

    headers = mock_api.last_upload_headers
    assert headers.get("content-length") == str(source.stat().st_size)
    assert "transfer-encoding" not in headers, \
        "chunked не должен появляться рядом с Content-Length"


def test_upload_file_sends_all_bytes(mock_api, tmp_path):
    """Файл доходит целиком, прогресс доходит до 1.0."""
    payload = b"y" * (2 * 1024 * 1024 + 123)   # не кратно блоку чтения
    source = tmp_path / "video.mp4"
    source.write_bytes(payload)

    progress = []
    result = YaDiskClient("t").upload_file(
        str(source), "/Archive/video.mp4", progress.append
    )

    assert result.size == len(payload)
    assert mock_api.received_bytes == len(payload)
    assert progress[-1] == 1.0


def test_large_file_upload(mock_api, tmp_path):
    """Файл больше блока чтения передаётся без потерь."""
    size = 5 * 1024 * 1024 + 999
    source = tmp_path / "big.mp4"
    source.write_bytes(b"z" * size)

    YaDiskClient("t").upload_file(str(source), "/Archive/big.mp4")
    assert mock_api.received_bytes == size


def test_upload_link_uses_get_with_path(mock_api):
    """Ссылка запрашивается GET-ом и с параметром path."""
    href, method = YaDiskClient("t")._get_upload_link("/Archive/video.mp4")
    assert method == "PUT"
    assert mock_api.upload_link_query["path"] == ["/Archive/video.mp4"]


def test_folder_created_before_upload(mock_api, tmp_path):
    """Папки создаются до загрузки, включая вложенные."""
    source = tmp_path / "v.mp4"
    source.write_bytes(b"data")

    YaDiskClient("t").upload_file(str(source), "/NewFolder/sub/v.mp4")

    assert "/NewFolder" in mock_api.created_folders
    assert "/NewFolder/sub" in mock_api.created_folders


def test_cyrillic_path_is_passed_intact(mock_api, tmp_path):
    """Кириллица в имени папки доходит без искажений."""
    source = tmp_path / "v.mp4"
    source.write_bytes(b"data")

    YaDiskClient("t").upload_file(str(source), "/тествыгрузка/v.mp4")
    assert mock_api.upload_link_query["path"] == ["/тествыгрузка/v.mp4"]


def test_post_without_url_would_fail(mock_api):
    """
    Подтверждает прошлый диагноз: POST без url — ошибка FieldValidationError.
    Это была ПЕРВАЯ ошибка, исправленная переходом на GET.
    """
    import requests
    r = requests.post(
        "http://127.0.0.1:%d/v1/disk/resources/upload"
        % mock_api.server_address[1],
        params={"path": "/a.mp4", "overwrite": "true"},
    )
    assert r.status_code == 400
    assert "url" in r.json()["description"]


# ---------------- отмена и ошибки ----------------

def test_cancel_before_start(tmp_path):
    source = tmp_path / "v.mp4"
    source.write_bytes(b"data")
    event = threading.Event()
    event.set()

    with pytest.raises(UploadCancelled):
        YaDiskClient("t").upload_file(str(source), "/x", cancel_event=event)


def test_empty_file_is_rejected(tmp_path):
    source = tmp_path / "empty.mp4"
    source.write_bytes(b"")

    with pytest.raises(YaDiskError, match="пустой"):
        YaDiskClient("t").upload_file(str(source), "/x")


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(YaDiskError, match="не найден"):
        YaDiskClient("t").upload_file(str(tmp_path / "nope.mp4"), "/x")


def test_html_error_gets_readable_message(mock_api, tmp_path):
    """
    Если сервер вернул HTML вместо JSON, в сообщении не должно быть
    свалки из тегов — пользователь должен понять смысл.
    """
    from dahua_exporter import yandex_disk as module

    class FakeResponse:
        status_code = 400
        text = "<html><head><title>400 Bad Request</title></head></html>"

    err = module._human_status(FakeResponse(), "Не удалось загрузить файл")
    assert "<html>" not in str(err)
    assert "nginx" not in str(err)
