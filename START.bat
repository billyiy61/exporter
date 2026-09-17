@echo off
REM ============================================================
REM  Dahua Exporter - лаунчер
REM
REM  Что умеет:
REM    1. Находит Python (py / python / python3) и проверяет, что он рабочий
REM    2. Проверяет зависимости и предлагает доустановить
REM    3. Предупреждает, если нет ffmpeg
REM    4. Чистит кэш __pycache__ перед запуском
REM    5. Запускает программу и показывает ошибку, если она упала
REM
REM  Запуск без аргументов открывает меню.
REM  Для ярлыка без окна консоли используй "Запустить без окна.vbs".
REM ============================================================

cd /d "%~dp0"
chcp 65001 >nul
setlocal enabledelayedexpansion

set "PROJECT=%~dp0"
set "SRC=%PROJECT%src"
set "PY="
set "PYW="

call :find_python
if not defined PY goto :no_python

if /i "%~1"=="run"    goto :ensure_deps
if /i "%~1"=="setup"  goto :need_deps
if /i "%~1"=="diag"   goto :run_diag
if /i "%~1"=="logs"   goto :open_logs
if /i "%~1"=="silent" goto :launch_silent
if /i "%~1"=="help"   goto :show_help
goto :menu


REM ============================================================
REM  Поиск рабочего Python
REM ============================================================
:find_python

REM  Ловушка Windows: в PATH может лежать пустая заглушка python.exe
REM  из Microsoft Store. Поэтому мало найти команду - надо убедиться,
REM  что она реально запускает интерпретатор.

py -3 -c "import sys" >nul 2>nul
if !errorlevel!==0 (
    set "PY=py -3"
    set "PYW=pyw -3"
    goto :eof
)

python -c "import sys" >nul 2>nul
if !errorlevel!==0 (
    set "PY=python"
    set "PYW=pythonw"
    goto :eof
)

python3 -c "import sys" >nul 2>nul
if !errorlevel!==0 (
    set "PY=python3"
    set "PYW=pythonw"
    goto :eof
)

goto :eof


REM ============================================================
REM  Меню
REM ============================================================
:menu
cls
echo.
echo   ==========================================
echo     Dahua Exporter
echo   ==========================================
echo.

set "DEPS_OK="
call :check_deps
if defined DEPS_OK (
    echo     Зависимости . . . установлены
) else (
    echo     Зависимости . . . НЕ УСТАНОВЛЕНЫ
)

call :has_ffmpeg
if !errorlevel!==0 (
    echo     ffmpeg  . . . . . найден
) else (
    echo     ffmpeg  . . . . . НЕ НАЙДЕН
)

echo.
echo   ------------------------------------------
echo     [1]  Запустить программу
echo     [2]  Установить зависимости
echo     [3]  Диагностика записей
echo     [4]  Открыть журнал
echo     [5]  Открыть папку проекта
echo     [0]  Выход
echo   ------------------------------------------
echo.
set "CHOICE="
set /p "CHOICE=   Выбор: "

if "!CHOICE!"=="1" goto :ensure_deps
if "!CHOICE!"=="2" goto :need_deps
if "!CHOICE!"=="3" goto :run_diag
if "!CHOICE!"=="4" goto :open_logs
if "!CHOICE!"=="5" goto :open_folder
if "!CHOICE!"=="0" goto :end
goto :menu


REM ============================================================
REM  Вспомогательные проверки
REM ============================================================
:check_deps
set "DEPS_OK="
%PY% -c "import PySide6, requests, keyring" >nul 2>nul
if !errorlevel!==0 set "DEPS_OK=1"
goto :eof


:has_ffmpeg
where ffmpeg >nul 2>nul
exit /b !errorlevel!


:ensure_deps
call :check_deps
if not defined DEPS_OK goto :need_deps
goto :launch


REM ============================================================
REM  Установка зависимостей
REM ============================================================
:need_deps
cls
echo.
echo   ==========================================
echo     Установка зависимостей
echo   ==========================================
echo.
echo   Python: %PY%
echo.
echo   Будет установлено:
echo     PySide6   - интерфейс
echo     requests  - сеть
echo     keyring   - хранение пароля
echo.
echo   Первый раз это занимает минуту-две.
echo.
pause

echo.
echo   Обновляю pip...
%PY% -m pip install --upgrade pip --quiet

echo.
echo   Устанавливаю пакеты...
%PY% -m pip install PySide6 requests keyring

if !errorlevel! neq 0 (
    echo.
    echo   [ОШИБКА] Пакеты не установились.
    echo.
    echo   Проверь:
    echo     - есть ли интернет
    echo     - Python версии 3.10 или новее
    echo.
    pause
    goto :menu
)

echo.
echo   Готово.
echo.
pause
goto :menu


REM ============================================================
REM  Запуск
REM ============================================================
:launch
cls
echo.
echo   Запускаю Dahua Exporter...
echo.

call :has_ffmpeg
if !errorlevel! neq 0 (
    echo   [ВНИМАНИЕ] ffmpeg не найден - выгрузка видео работать не будет.
    echo   Установка:  winget install Gyan.FFmpeg
    echo.
    timeout /t 5 >nul
)

REM  Чистим кэш: иначе Python может выполнить старый код,
REM  и правки в файлах не дадут эффекта.
if exist "%SRC%\dahua_exporter\__pycache__" (
    rd /s /q "%SRC%\dahua_exporter\__pycache__" >nul 2>nul
)

cd /d "%PROJECT%"
%PY% -c "import sys; sys.path.insert(0, 'src'); from dahua_exporter.gui import main; sys.exit(main())"
set "RC=!errorlevel!"

if !RC! neq 0 (
    echo.
    echo   ==========================================
    echo     Программа завершилась с кодом !RC!
    echo   ==========================================
    echo.
    echo   Посмотри журнал: пункт 4 в меню.
    echo.
    pause
    goto :menu
)
goto :end


:launch_silent
call :check_deps
if not defined DEPS_OK goto :need_deps

if defined PYW (
    start "" %PYW% -c "import sys; sys.path.insert(0, r'%SRC%'); from dahua_exporter.gui import main; main()"
    goto :end
)
goto :launch


REM ============================================================
REM  Диагностика
REM ============================================================
:run_diag
call :check_deps
if not defined DEPS_OK goto :need_deps

cls
echo.
echo   ==========================================
echo     Диагностика записей
echo   ==========================================
echo.
set "DHOST="
set "DUSER="
set /p "DHOST=   Адрес регистратора: "
set /p "DUSER=   Логин: "
set /p "DPASS=   Пароль: "

if "!DHOST!"=="" goto :menu
if "!DUSER!"=="" goto :menu

echo.
echo   Ищу записи на канале 1...
echo.

%PY% -c "import sys; sys.path.insert(0, 'src'); from dahua_exporter import diagnose as d; d.run(r'!DHOST!', 80, r'!DUSER!', r'!DPASS!', 1)"
echo.
echo   ==== Скопируй вывод выше и пришли его ====
pause
goto :menu


REM ============================================================
REM  Журнал и папка
REM ============================================================
:open_logs
set "LOGDIR=%USERPROFILE%\.dahua_exporter\logs"
if not exist "!LOGDIR!" (
    echo.
    echo   Журнал ещё не создан - программа не запускалась.
    echo.
    pause
    goto :menu
)
if exist "!LOGDIR!\dahua_exporter.log" (
    start "" notepad "!LOGDIR!\dahua_exporter.log"
) else (
    start "" explorer "!LOGDIR!"
)
goto :menu


:open_folder
start "" explorer "%PROJECT%"
goto :menu


:show_help
echo.
echo   Dahua Exporter - лаунчер
echo.
echo   Без аргументов открывается меню.
echo.
echo   Аргументы:
echo     run      запустить сразу
echo     setup    установить зависимости
echo     diag     диагностика
echo     logs     журнал
echo     silent   без окна консоли
echo.
goto :end


:no_python
cls
echo.
echo   ==========================================
echo     Python не найден
echo   ==========================================
echo.
echo   Установи Python 3.10 или новее:
echo.
echo       https://www.python.org/downloads
echo.
echo   ВАЖНО: при установке поставь галочку
echo          "Add Python to PATH"
echo.
pause
goto :end


:end
endlocal
exit /b 0
