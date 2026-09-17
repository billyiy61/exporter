@echo off
REM ============================================================
REM  Диагностика поиска записей Dahua.
REM  Просто запусти этот файл двойным кликом ИЛИ из cmd.
REM  Правь значения ниже под себя.
REM ============================================================

set HOST=10.57.229.191
set USER=bgPHf4sq
set PASS=Video2021wb
set CHANNEL=1
REM -- ВАЖНО: поставь ДЕНЬ, за который на камере точно есть запись --
set START=2026-09-15 09:00:00
set END=2026-09-15 11:00:00

echo Диагностика: %HOST% канал %CHANNEL% интервал %START% - %END%
echo.

py -c "import sys; sys.path.insert(0,'src'); from dahua_exporter import diagnose as d; d.run('%HOST%', 80, '%USER%', '%PASS%', %CHANNEL%, '%START%', '%END%')"

echo.
echo ==== Скопируй ВЕСЬ вывод выше и пришли его ====
pause
