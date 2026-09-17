"""
Клиент Dahua NVR/DVR/IP-камеры через официальный HTTP CGI API.

Работает только при прямом сетевом доступе к устройству (локальная сеть,
VPN или проброс порта). P2P/облако Dahua здесь СОЗНАТЕЛЬНО не используется.

ВАЖНЫЙ ФАКТ, подтверждённый на живом NVR (DHI-NVR4108-8P-4KS2/L):
устройство повторяет префикс 'items[N].' ПЕРЕД КАЖДЫМ полем:

    found=1
    items[0].VideoStream=Main
    items[0].Channel=0
    items[0].Type=dav
    items[0].StartTime=2026-09-15 00:00:00
    items[0].EndTime=2026-09-15 01:00:00
    items[0].FilePath=/mnt/dvr/...
    items[0].Length=309460992

Поэтому НЕЛЬЗЯ резать ответ по re.split(r"items\\[\\d+\\]\\.") — это даст
куски по одному полю и парсер не найдёт полной записи. Нужно группировать
поля по индексу записи. См. _parse_found_page().
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator
from urllib.parse import quote

import requests
from requests.auth import HTTPDigestAuth
from requests.exceptions import ConnectionError as ReqConnectionError


class DahuaError(Exception):
    """Ошибки взаимодействия с устройством."""


@dataclass
class Channel:
    number: int
    name: str


@dataclass
class Recording:
    channel: int
    start: datetime
    end: datetime
    file_path: str
    file_length: int
    rec_type: str


@dataclass
class ConnectionInfo:
    host: str
    port: int = 80
    username: str = "admin"
    password: str = ""
    use_https: bool = False

    @property
    def base_url(self) -> str:
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{self.host}:{self.port}"


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")


class DahuaClient:
    def __init__(self, info: ConnectionInfo, timeout: int = 15):
        self.info = info
        self.timeout = timeout
        self._session = requests.Session()
        self._auth = HTTPDigestAuth(info.username, info.password)
        self._find_object_id: str | None = None

    # ---------- низкоуровневый запрос ----------

    def _get(self, path: str, **params) -> requests.Response:
        url = f"{self.info.base_url}{path}"
        try:
            resp = self._session.get(
                url, params=params, auth=self._auth, timeout=self.timeout
            )
        except ReqConnectionError as exc:
            raise DahuaError(
                f"Нет связи с {self.info.host}:{self.info.port}. "
                f"Проверь IP, порт и что ПК в той же сети. ({exc})"
            ) from exc

        if resp.status_code == 401:
            raise DahuaError("Неверный логин или пароль устройства (401).")
        if resp.status_code >= 400:
            raise DahuaError(
                f"Устройство вернуло HTTP {resp.status_code} на {path}."
            )
        return resp

    # ---------- проверка связи ----------

    def test_connection(self) -> str:
        return self._get("/cgi-bin/magicBox.cgi", action="getDeviceType").text.strip()

    def get_serial(self) -> str:
        return self._get("/cgi-bin/magicBox.cgi", action="getSerialNo").text.strip()

    # ---------- каналы ----------

    def get_channels(self) -> list[Channel]:
        resp = self._get(
            "/cgi-bin/configManager.cgi", action="getConfig", name="ChannelTitle"
        )
        channels: dict[int, str] = {}
        text = resp.text.replace("\r", "")
        for line in text.splitlines():
            m = re.match(r"\s*table\.ChannelTitle\[(\d+)\]\.Name=\s*(.*)", line)
            if m:
                channels[int(m.group(1))] = m.group(2).strip()
        if not channels:
            return [Channel(number=i + 1, name=f"Канал {i + 1}") for i in range(8)]
        # configManager нумерует с 0, API записи работает с 1
        return [
            Channel(number=idx + 1, name=name or f"Канал {idx + 1}")
            for idx, name in sorted(channels.items())
        ]

    # ---------- поиск записей ----------

    def find_recordings(
        self, channel: int, start: datetime, end: datetime, rec_type: str = "dav"
    ) -> list[Recording]:
        self._create_find_object()
        try:
            self._start_find(channel, start, end, rec_type)
            return list(self._iter_found(channel))
        finally:
            self._close_find_object()

    def _create_find_object(self) -> None:
        resp = self._get("/cgi-bin/mediaFileFind.cgi", action="factory.create")
        m = re.search(r"result=(\d+)", resp.text)
        if not m:
            raise DahuaError(f"Не удалось создать объект поиска: {resp.text[:200]}")
        self._find_object_id = m.group(1)

    def _start_find(
        self, channel: int, start: datetime, end: datetime, rec_type: str
    ) -> None:
        params = {
            "action": "findFile",
            "object": self._find_object_id,
            "condition.Channel": str(channel),
            "condition.StartTime": start.strftime("%Y-%m-%d %H:%M:%S"),
            "condition.EndTime": end.strftime("%Y-%m-%d %H:%M:%S"),
            "condition.Types[0]": rec_type,
        }
        resp = self._get("/cgi-bin/mediaFileFind.cgi", **params)
        if not resp.text.strip().startswith("OK"):
            raise DahuaError(f"findFile вернул не OK: {resp.text[:200]}")

    def _iter_found(self, channel: int) -> Iterator[Recording]:
        """Постранично тянем найденные файлы."""
        while True:
            resp = self._get(
                "/cgi-bin/mediaFileFind.cgi",
                action="findNextFile",
                object=self._find_object_id,
                count="100",
            )
            text = resp.text
            # ВНИМАНИЕ: проверяем 'found=0' как отдельный признак ПЕРВОЙ строки,
            # а не подстроку: 'items[0].Channel=0' содержит '=0'.
            if re.search(r"(^|\n|\r)\s*found=0\s*\r?(\n|$)", text):
                break

            recs = list(self._parse_found_page(text, channel))
            if not recs:
                break
            yield from recs

    @staticmethod
    def _parse_found_page(text: str, default_channel: int) -> Iterator[Recording]:
        """
        КЛЮЧЕВАЯ функция. Устройство повторяет 'items[N].' перед КАЖДЫМ
        полем, поэтому:

          1) находим все уникальные индексы N;
          2) для каждого N собираем поля, сняв префикс 'items[N].';
          3) парсим получившийся блок.

        Пример входа (упрощённо, с реального NVR):
            found=1\r\n
            items[0].VideoStream=Main\r\n
            items[0].Channel=0\r\n
            items[0].StartTime=2026-09-15 00:00:00\r\n
            ...
        """
        text = text.replace("\r", "")

        # все индексы записей на этой странице
        indices = re.findall(r"items\[(\d+)\]\.", text)
        seen = []
        for i in indices:
            if i not in seen:
                seen.append(i)

        for idx in seen:
            # берём строки, относящиеся к этой записи, и убираем префикс
            fields: dict[str, str] = {}
            for m in re.finditer(rf"items\[{idx}\]\.([A-Za-z0-9_]+)=(.*)", text):
                key, val = m.group(1), m.group(2).strip()
                fields.setdefault(key, val)

            start_raw = fields.get("StartTime")
            end_raw = fields.get("EndTime")
            path = fields.get("FilePath")
            if not (start_raw and end_raw and path):
                continue

            try:
                start_dt = _parse_time(start_raw)
                end_dt = _parse_time(end_raw)
            except ValueError:
                continue

            try:
                length = int(fields.get("Length") or 0)
            except ValueError:
                length = 0

            # Channel устройство может отдать как 0-based (0 = 1-й канал)
            ch_raw = fields.get("Channel")
            try:
                ch = int(ch_raw) if ch_raw is not None else default_channel
            except ValueError:
                ch = default_channel
            # нормализуем: если 0 — считаем это первым каналом
            if ch == 0:
                ch = 1

            yield Recording(
                channel=ch,
                start=start_dt,
                end=end_dt,
                file_path=path,
                file_length=length,
                rec_type=fields.get("Type") or "dav",
            )

    def _close_find_object(self) -> None:
        if not self._find_object_id:
            return
        try:
            self._get(
                "/cgi-bin/mediaFileFind.cgi",
                action="close",
                object=self._find_object_id,
            )
        except DahuaError:
            pass
        self._find_object_id = None

    # ---------- RTSP ----------

    def build_rtsp_url(
        self, channel: int, start: datetime, end: datetime, subtype: int = 0
    ) -> str:
        user = quote(self.info.username, safe="")
        pwd = quote(self.info.password, safe="")
        st = start.strftime("%Y_%m_%d_%H_%M_%S")
        et = end.strftime("%Y_%m_%d_%H_%M_%S")
        return (
            f"rtsp://{user}:{pwd}@{self.info.host}:554/"
            f"cam/playback?channel={channel}&subtype={subtype}"
            f"&starttime={st}&endtime={et}"
        )
