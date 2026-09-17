"""
Клиент REST API Яндекс.Диска.

Загрузка файла — ровно по официальному протоколу:
  1. GET  /resources/upload?path=...&overwrite=true  -> {href, method}
  2. PUT  <href>  с телом файла (одним запросом, потоково)

Ссылка из шага 1 действует 30 минут. Дальше это обычный HTTP PUT —
никаких сессий и кусков с part_id в реальном API Яндекса нет, прогресс
считаем по мере чтения файла с диска и потоковой передачи в запрос.

ПОЧЕМУ НЕ ГЕНЕРАТОР (важно, была ошибка 400)
--------------------------------------------
Раньше тело запроса отдавалось генератором, и рядом вручную
добавлялся заголовок Content-Length. Это ломает запрос:

  * если data= — генератор, requests НЕ знает длину заранее и сам
    ставит Transfer-Encoding: chunked;
  * при этом руками выставленный Content-Length никуда не исчезает.

Сервер получает оба заголовка сразу. По протоколу HTTP это
взаимоисключающие вещи, и серверы на nginx отвечают
"400 Bad Request" обычной HTML-страницей.

Теперь тело — открытый файловый объект, обёрнутый в счётчик.
requests сам определяет размер по file.tell()/seek() и ставит
правильный Content-Length, chunked не появляется, а прогресс
по-прежнему обновляется по ходу передачи.

Локальные ограничения и место: Яндекс может ответить 423 (лимит загрузок
исчерпан) или 507 (нет места). Эти случаи переведены в понятные
русские сообщения, чтобы пользователь понимал, что делать.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable

import requests

API_BASE = "https://cloud-api.yandex.net/v1/disk"

# Размер блока чтения с диска при потоковой передаче — влияет только
# на частоту обновления прогресса, не на сетевые запросы (запрос один).
READ_CHUNK = 1 * 1024 * 1024

# максимальное число попыток при сетевых сбоях
MAX_RETRIES = 3


class YaDiskError(Exception):
    """Ошибка работы с Яндекс.Диском."""


class UploadCancelled(YaDiskError):
    """Загрузка отменена пользователем."""


@dataclass
class DiskItem:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    modified: str = ""


@dataclass
class UploadResult:
    cloud_path: str
    size: int
    elapsed: float


@dataclass
class DiskInfo:
    login: str
    display_name: str
    total_space: int
    used_space: int

    @property
    def free_space(self) -> int:
        return max(self.total_space - self.used_space, 0)


def _looks_like_html(text: str) -> bool:
    """Ответ от nginx/прокси приходит в HTML, а не в JSON."""
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


def _human_status(r: requests.Response, context: str) -> YaDiskError:
    """Переводит коды ошибок Яндекс.Диска в понятные сообщения."""
    code = r.status_code
    if code == 401:
        return YaDiskError(
            "Токен недействителен или отозван. Войди в аккаунт заново."
        )
    if code == 403:
        return YaDiskError(
            "Нет прав на эту операцию. Проверь, что при входе "
            "приложение получило доступ к Диску."
        )
    if code == 404:
        return YaDiskError(
            "Путь не найден. Возможно, папка была удалена или "
            "переименована в другом месте."
        )
    if code == 409:
        return YaDiskError(
            "Конфликт: папка с таким именем уже существует, "
            "а перезапись запрещена."
        )
    if code == 413:
        return YaDiskError(
            "Файл слишком большой для твоего тарифа Яндекс 360."
        )
    if code == 423:
        return YaDiskError(
            "Загрузка недоступна: исчерпан месячный лимит загрузок "
            "на Диск, либо ведутся технические работы. "
            "Лимит обычно обновляется в начале календарного месяца."
        )
    if code == 429:
        return YaDiskError(
            "Слишком много запросов подряд. Подожди немного и повтори."
        )
    if code == 507:
        return YaDiskError(
            "На Яндекс.Диске не хватает свободного места. "
            "Освободи место или смени тариф."
        )
    if code >= 500:
        return YaDiskError(
            f"Сервер Яндекса временно недоступен (HTTP {code}). "
            f"Попробуй позже."
        )
    if _looks_like_html(r.text):
        # Так отвечает nginx, когда запрос отвергнут целиком.
        return YaDiskError(
            f"{context}: сервер отклонил запрос (HTTP {code}). "
            f"Если это повторяется — сообщи разработчику."
        )
    return YaDiskError(f"{context}: HTTP {code}. {r.text[:200]}")


class _ProgressFile:
    """
    Обёртка над открытым файлом, которая сообщает о прочитанных байтах.

    Зачем: requests должен получить НАСТОЯЩИЙ файловый объект, чтобы
    сам посчитать Content-Length. Но тогда мы теряем контроль над
    прогрессом. Обёртка решает оба вопроса: снаружи ведёт себя как
    файл (поддерживает read, seek, tell, fileno), а внутри считает
    прочитанное и дёргает on_progress.
    """

    def __init__(self, file_obj, total: int,
                 on_progress: Callable[[float], None] | None,
                 cancel_event: threading.Event | None):
        self._f = file_obj
        self._total = total
        self._on_progress = on_progress
        self._cancel = cancel_event
        self._sent = 0
        self._last_report = 0.0

    # --- то, что использует requests ---

    def read(self, size: int = -1) -> bytes:
        if self._cancel is not None and self._cancel.is_set():
            raise UploadCancelled("Загрузка отменена.")
        data = self._f.read(size)
        if data:
            self._sent += len(data)
            self._report()
        return data

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        """Нужен requests для определения размера файла."""
        result = self._f.seek(offset, whence)
        # после перемотки счётчик сбрасываем: либо в начало (повтор),
        # либо в конец (замер размера)
        if whence == os.SEEK_SET and offset == 0:
            self._sent = 0
            self._last_report = 0.0
        elif whence == os.SEEK_END:
            self._sent = self._total
        return result

    def tell(self) -> int:
        return self._f.tell()

    def fileno(self) -> int:
        return self._f.fileno()

    def __len__(self) -> int:
        return self._total

    # --- прогресс ---

    def _report(self) -> None:
        if not self._on_progress or not self._total:
            return
        now = time.time()
        # не чаще 10 раз в секунду, и обязательно на последнем блоке
        if (now - self._last_report >= 0.1
                or self._sent >= self._total):
            self._on_progress(min(self._sent / self._total, 1.0))
            self._last_report = now

    # --- контекстный менеджер: files закроются сами ---

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()
        return False


class YaDiskClient:
    """Клиент API Яндекс.Диска для одного аккаунта."""

    def __init__(self, token: str, timeout: int = 30):
        self.timeout = timeout
        self.token = token
        self._s = requests.Session()
        self._s.headers["Authorization"] = f"OAuth {token}"

    # ---------- информация ----------

    def get_info(self) -> DiskInfo:
        r = self._safe_get(f"{API_BASE}/")
        j = r.json()
        user = j.get("user", {})
        return DiskInfo(
            login=user.get("login", ""),
            display_name=(user.get("display_name")
                          or user.get("real_name")
                          or user.get("login", "")),
            total_space=int(j.get("total_space", 0)),
            used_space=int(j.get("used_space", 0)),
        )

    def check_token(self) -> bool:
        try:
            self.get_info()
            return True
        except YaDiskError:
            return False

    # ---------- папки ----------

    def list_folder(self, path: str = "/") -> list[DiskItem]:
        """
        Содержимое папки. Возвращает СНАЧАЛА папки, потом файлы,
        внутри каждой группы — по алфавиту.
        """
        if not path.startswith("/"):
            path = "/" + path

        r = self._safe_get(
            f"{API_BASE}/resources",
            params={"path": path, "limit": 500,
                    "sort": "name", "preview_size": "S"},
        )
        j = r.json()
        items = j.get("_embedded", {}).get("items", [])

        result = [
            DiskItem(
                name=it.get("name", ""),
                path=it.get("path", ""),
                is_dir=it.get("type") == "dir",
                size=int(it.get("size", 0) or 0),
                modified=it.get("modified", ""),
            )
            for it in items
        ]
        result.sort(key=lambda x: (not x.is_dir, x.name.lower()))
        return result

    def folder_exists(self, path: str) -> bool:
        try:
            self._safe_get(
                f"{API_BASE}/resources",
                params={"path": path, "limit": 1},
            )
            return True
        except YaDiskError:
            return False

    def create_folder(self, path: str) -> None:
        r = self._s.put(
            f"{API_BASE}/resources",
            params={"path": path},
            timeout=self.timeout,
        )
        if r.status_code == 409:
            return   # уже существует — это не ошибка для нас
        if r.status_code not in (200, 201):
            raise _human_status(r, "Не удалось создать папку")

    def ensure_folder(self, path: str) -> None:
        """Создаёт папку со всеми родителями, если её нет."""
        if not path or path == "/":
            return
        parts = [p for p in path.strip("/").split("/") if p]
        current = ""
        for part in parts:
            current += "/" + part
            if not self.folder_exists(current):
                self.create_folder(current)

    # ---------- загрузка ----------

    def upload_file(
        self,
        local_path: str,
        cloud_path: str,
        on_progress: Callable[[float], None] | None = None,
        cancel_event: threading.Event | None = None,
        chunk_size: int = READ_CHUNK,
    ) -> UploadResult:
        """
        Загружает файл по официальному протоколу Яндекс.Диска:
        GET за ссылкой, затем один PUT с телом файла.

        Тело — открытый файл в обёртке _ProgressFile. Именно так
        requests выставляет корректный Content-Length; генератор
        здесь использовать НЕЛЬЗЯ (см. объяснение в начале файла).

        on_progress получает 0.0..1.0, вызывается не чаще 10 раз в
        секунду, чтобы не грузить GUI.
        """
        t0 = time.time()

        if not os.path.exists(local_path):
            raise YaDiskError(f"Локальный файл не найден: {local_path}")

        total = os.path.getsize(local_path)
        if total == 0:
            raise YaDiskError("Файл пустой — загружать нечего.")

        if cancel_event is not None and cancel_event.is_set():
            raise UploadCancelled("Загрузка отменена.")

        # 0) папка назначения должна существовать, иначе Яндекс
        #    откажется принимать файл (404 / FieldValidationError)
        folder = cloud_path.rsplit("/", 1)[0]
        if folder:
            if cancel_event is not None and cancel_event.is_set():
                raise UploadCancelled("Загрузка отменена.")
            self.ensure_folder(folder)

        # 1) получаем ссылку и метод для заливки (обычно PUT)
        href, method = self._get_upload_link(cloud_path)

        # 2) отправляем файл одним запросом по полученной ссылке
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise UploadCancelled("Загрузка отменена.")
            try:
                with open(local_path, "rb") as raw:
                    body = _ProgressFile(raw, total, on_progress, cancel_event)
                    r = self._s.request(
                        method, href,
                        data=body,
                        # Content-Length requests выставит сам по размеру
                        # файла. Вручную его НЕ добавляем: рядом с ним
                        # появился бы ещё и Transfer-Encoding: chunked,
                        # и сервер ответил бы 400 Bad Request.
                        timeout=(self.timeout, None),
                    )
            except UploadCancelled:
                raise
            except requests.RequestException as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    time.sleep(1.5 * attempt)
                    continue
                raise YaDiskError(
                    f"Сеть недоступна при загрузке файла: {e}"
                ) from e

            if r.status_code in (200, 201, 202):
                break
            if r.status_code >= 500 and attempt < MAX_RETRIES:
                last_error = r
                time.sleep(1.5 * attempt)
                continue
            raise _human_status(r, "Не удалось загрузить файл")
        else:
            raise YaDiskError(f"Файл не удалось загрузить: {last_error}")

        if on_progress:
            on_progress(1.0)

        return UploadResult(
            cloud_path=cloud_path,
            size=total,
            elapsed=time.time() - t0,
        )

    def _get_upload_link(self, cloud_path: str) -> tuple[str, str]:
        """
        Шаг 1 официального протокола: получаем ссылку и HTTP-метод
        для заливки файла. Действует 30 минут.

        ВАЖНО: это GET, а не POST. POST /resources/upload — другой
        метод API Яндекса ("загрузить файл ПО ССЫЛКЕ ИЗ ИНТЕРНЕТА",
        ожидает параметр "url"). Если сюда отправить обычный POST
        для заливки локального файла, Яндекс отвечает
        HTTP 400 FieldValidationError "url": это поле обязательно.
        """
        r = self._s.get(
            f"{API_BASE}/resources/upload",
            params={"path": cloud_path, "overwrite": "true"},
            timeout=self.timeout,
        )
        if r.status_code not in (200, 201, 202):
            raise _human_status(r, "Не удалось начать загрузку")
        j = r.json()
        href = j.get("href")
        method = j.get("method") or "PUT"
        if not href:
            raise YaDiskError(f"Яндекс не вернул ссылку загрузки: {j}")
        return href, method

    # ---------- удаление ----------

    def delete(self, cloud_path: str, permanently: bool = True) -> None:
        r = self._s.delete(
            f"{API_BASE}/resources",
            params={"path": cloud_path,
                    "permanently": "true" if permanently else "false"},
            timeout=self.timeout,
        )
        if r.status_code not in (200, 202, 204):
            raise _human_status(r, "Не удалось удалить файл")

    def file_exists(self, cloud_path: str) -> bool:
        """Проверяет, что файл реально лежит в облаке."""
        try:
            r = self._safe_get(
                f"{API_BASE}/resources",
                params={"path": cloud_path, "limit": 1},
            )
            j = r.json()
            return j.get("type") == "file"
        except YaDiskError:
            return False

    # ---------- низкоуровневое ----------

    def _safe_get(self, url: str, params: dict | None = None) -> requests.Response:
        try:
            r = self._s.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise YaDiskError(
                f"Не удалось связаться с Яндекс.Диском: {e}"
            ) from e
        if r.status_code >= 400:
            raise _human_status(r, "Ошибка запроса")
        return r
