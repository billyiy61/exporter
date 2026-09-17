"""Диагностика поиска записей: показывает СЫРЫЕ ответы устройства.

Запуск из папки проекта:
    py -c "import sys; sys.path.insert(0,'src'); from dahua_exporter import diagnose as d; d.run('10.57.229.191', 80, 'bgPHf4sq', 'Video2021wb', 1, '2026-09-15 09:00:00', '2026-09-15 11:00:00')"

Печатает:
  * ответ factory.create (id объекта поиска)
  * ответ findFile (что устройство думает о нашем условии)
  * ответ findNextFile (главное — реальный формат найденных записей)

Ничего не парсит, просто кажет как есть. По этому выводу видно,
какой диалект CGI у устройства и как подстроить парсер.
"""

from __future__ import annotations

import sys


def run(host, port, user, password, channel, start, end):
    # даём возможности импортировать package из src, если не установлен
    from .dahua_client import ConnectionInfo, DahuaClient

    c = DahuaClient(ConnectionInfo(host, port, user, password))

    print("=" * 70)
    print("1) factory.create — создаём объект поиска")
    print("=" * 70)
    r1 = c._get("/cgi-bin/mediaFileFind.cgi", action="factory.create")
    print("RAW:", repr(r1.text))
    print()

    # вытащим id, печатаем как есть
    import re
    m = re.search(r"result=(\d+)", r1.text)
    obj = m.group(1) if m else None
    print("object id:", obj)
    print()

    if not obj:
        print("!! Не получили object id — дальше нет смысла. ")
        print("   Смотрим выше: возможно, другое имя action.")
        return

    print("=" * 70)
    print("2) findFile — задаём условие поиска")
    print("=" * 70)
    cond = {
        "condition.Channel": str(channel),
        "condition.StartTime": start,
        "condition.EndTime": end,
        "condition.Types[0]": "dav",
    }
    print("отправляем параметры:", cond)
    r2 = c._get("/cgi-bin/mediaFileFind.cgi", action="findFile", object=obj, **cond)
    print("RAW:", repr(r2.text))
    print()

    print("=" * 70)
    print("3) findNextFile — тянем найденные записи")
    print("=" * 70)
    for i in range(3):
        r3 = c._get(
            "/cgi-bin/mediaFileFind.cgi",
            action="findNextFile",
            object=obj,
            count="100",
        )
        print(f"--- страница {i + 1} ---")
        print("RAW:", repr(r3.text))
        if "found=0" in r3.text:
            break
        print()

    print("=" * 70)
    print("4) close — закрываем объект поиска")
    print("=" * 70)
    try:
        r4 = c._get("/cgi-bin/mediaFileFind.cgi", action="close", object=obj)
        print("RAW:", repr(r4.text))
    except Exception as e:
        print("close вернул:", e)

    print()
    print("=" * 70)
    print("ЕСЛИ записи есть, но блок 'items[N]' не появился:")
    print("  устройство отдаёт другой формат. Пришли весь вывод —")
    print("  по нему подстроим парсер _parse_item.")
    print("=" * 70)


if __name__ == "__main__":
    if len(sys.argv) < 8:
        print(
            "usage: python -m dahua_exporter.diagnose "
            "<host> <port> <user> <password> <channel> "
            "'<start>' '<end>'"
        )
        sys.exit(1)
    run(
        sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4],
        int(sys.argv[5]), sys.argv[6], sys.argv[7],
    )
