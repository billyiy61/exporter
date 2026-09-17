#!/bin/bash
#############################################################
#  Dahua Exporter - launcher for macOS and Linux
#
#  Features:
#    1. Finds Python (python3, python)
#    2. Checks and installs dependencies
#    3. Checks ffmpeg presence
#    4. Cleans __pycache__ before launch
#    5. Shows error if program crashes
#
#  Run without arguments to open the menu.
#############################################################

set -e

# Navigate to the script's directory
cd "$(dirname "$0")"

PROJECT="$(pwd)"
SRC="$PROJECT/src"
PY=""

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

#############################################################
#  Find Python
#############################################################
find_python() {
    # macOS: python3 is standard after Big Sur
    # Linux: usually python3 or python
    if command -v python3 &> /dev/null; then
        if python3 -c "import sys" &> /dev/null; then
            PY="python3"
            return 0
        fi
    fi

    if command -v python &> /dev/null; then
        if python -c "import sys" &> /dev/null; then
            PY="python"
            return 0
        fi
    fi

    return 1
}

#############################################################
#  Check dependencies
#############################################################
check_deps() {
    $PY -c "import PySide6, requests, keyring" &> /dev/null
    return $?
}

#############################################################
#  Check ffmpeg
#############################################################
has_ffmpeg() {
    command -v ffmpeg &> /dev/null
    return $?
}

#############################################################
#  Menu
#############################################################
show_menu() {
    clear
    echo ""
    echo -e "${CYAN}  ==========================================${NC}"
    echo -e "${CYAN}    Dahua Exporter${NC}"
    echo -e "${CYAN}  ==========================================${NC}"
    echo ""

    # Check dependencies status
    if check_deps; then
        echo -e "    Зависимости:  ${GREEN}установлены${NC}"
    else
        echo -e "    Зависимости:  ${RED}НЕ УСТАНОВЛЕНЫ${NC}"
    fi

    # Check ffmpeg status
    if has_ffmpeg; then
        echo -e "    ffmpeg:       ${GREEN}найден${NC}"
    else
        echo -e "    ffmpeg:       ${RED}НЕ НАЙДЕН${NC}"
    fi

    echo ""
    echo "  ------------------------------------------"
    echo "    [1]  Запустить программу"
    echo "    [2]  Установить зависимости"
    echo "    [3]  Диагностика записей"
    echo "    [4]  Открыть журнал"
    echo "    [5]  Открыть папку проекта"
    echo "    [0]  Выход"
    echo "  ------------------------------------------"
    echo ""
    read -p "   Выбор: " choice

    case "$choice" in
        1) ensure_deps ;;
        2) install_deps ;;
        3) run_diag ;;
        4) open_logs ;;
        5) open_folder ;;
        0) exit 0 ;;
        *) show_menu ;;
    esac
}

#############################################################
#  Ensure dependencies are installed
#############################################################
ensure_deps() {
    if ! check_deps; then
        install_deps
    else
        launch
    fi
}

#############################################################
#  Install dependencies
#############################################################
install_deps() {
    clear
    echo ""
    echo -e "${CYAN}  ==========================================${NC}"
    echo -e "${CYAN}    Установка зависимостей${NC}"
    echo -e "${CYAN}  ==========================================${NC}"
    echo ""
    echo "  Python: $PY"
    echo ""
    echo "  Будут установлены:"
    echo "    - PySide6   (интерфейс)"
    echo "    - requests  (сеть)"
    echo "    - keyring   (хранение пароля)"
    echo ""
    echo "  Это займёт минуту-две при первом запуске."
    echo ""
    read -p "  Нажми Enter для продолжения..."

    echo ""
    echo "  Обновляю pip..."
    $PY -m pip install --upgrade pip --quiet

    echo ""
    echo "  Устанавливаю пакеты..."
    $PY -m pip install PySide6 requests keyring

    if [ $? -ne 0 ]; then
        echo ""
        echo -e "${RED}  [ОШИБКА] Не удалось установить пакеты.${NC}"
        echo ""
        echo "  Что можно сделать:"
        echo "    1. Проверь подключение к интернету"
        echo "    2. Обнови Python до версии 3.10 или новее"
        echo "    3. На macOS попробуй: brew install python@3.11"
        echo ""
        read -p "  Нажми Enter..."
        show_menu
        return 1
    fi

    echo ""
    echo -e "${GREEN}  Готово. Зависимости установлены.${NC}"
    echo ""
    read -p "  Нажми Enter..."
    show_menu
}

#############################################################
#  Launch the program
#############################################################
launch() {
    clear
    echo ""
    echo "  Запускаю Dahua Exporter..."
    echo ""

    # Check ffmpeg
    if ! has_ffmpeg; then
        echo -e "${YELLOW}  [ВНИМАНИЕ] ffmpeg не найден.${NC}"
        echo ""
        echo "  Без него программа не сможет выгружать видео."
        echo "  Установить можно так:"
        echo ""
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo "    brew install ffmpeg"
        else
            echo "    sudo apt install ffmpeg      # Debian/Ubuntu"
            echo "    sudo dnf install ffmpeg      # Fedora"
            echo "    sudo pacman -S ffmpeg        # Arch"
        fi
        echo ""
        sleep 3
    fi

    # Clean cache
    if [ -d "$SRC/dahua_exporter/__pycache__" ]; then
        rm -rf "$SRC/dahua_exporter/__pycache__"
    fi

    cd "$PROJECT"
    $PY -c "import sys; sys.path.insert(0, 'src'); from dahua_exporter.gui import main; sys.exit(main())"
    RC=$?

    if [ $RC -ne 0 ]; then
        echo ""
        echo -e "${RED}  ==========================================${NC}"
        echo -e "${RED}    Программа завершилась с кодом $RC${NC}"
        echo -e "${RED}  ==========================================${NC}"
        echo ""
        echo "  Что делать:"
        echo "    - Открой журнал: пункт 4 в меню"
        echo "    - Проверь зависимости: пункт 2 в меню"
        echo ""
        read -p "  Нажми Enter..."
        show_menu
    fi

    exit 0
}

#############################################################
#  Diagnostics
#############################################################
run_diag() {
    if ! check_deps; then
        install_deps
        return
    fi

    clear
    echo ""
    echo -e "${CYAN}  ==========================================${NC}"
    echo -e "${CYAN}    Диагностика поиска записей${NC}"
    echo -e "${CYAN}  ==========================================${NC}"
    echo ""
    echo "  Нужны данные регистратора."
    echo ""
    read -p "  Адрес регистратора [10.57.229.191]: " DHOST
    read -p "  Логин [admin]: " DUSER
    read -s -p "  Пароль: " DPASS
    echo ""

    DHOST="${DHOST:-10.57.229.191}"
    DUSER="${DUSER:-admin}"

    echo ""
    echo "  Ищу записи за последние сутки на канале 1..."
    echo ""

    $PY -c "import sys; sys.path.insert(0, 'src'); from dahua_exporter import diagnose as d; d.run('$DHOST', 80, '$DUSER', '$DPASS', 1)"

    echo ""
    echo "  ==== Скопируй весь вывод выше ===="
    read -p "  Нажми Enter..."
    show_menu
}

#############################################################
#  Open logs
#############################################################
open_logs() {
    LOGDIR="$HOME/.dahua_exporter/logs"

    if [ ! -d "$LOGDIR" ]; then
        echo ""
        echo "  Журнал ещё не создан — программа ни разу не запускалась."
        echo ""
        read -p "  Нажми Enter..."
        show_menu
        return
    fi

    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS: open with default text editor
        if [ -f "$LOGDIR/dahua_exporter.log" ]; then
            open -a TextEdit "$LOGDIR/dahua_exporter.log"
        else
            open "$LOGDIR"
        fi
    else
        # Linux: try common editors
        LOGFILE="$LOGDIR/dahua_exporter.log"
        if [ -f "$LOGFILE" ]; then
            if command -v xdg-open &> /dev/null; then
                xdg-open "$LOGFILE"
            elif command -v gedit &> /dev/null; then
                gedit "$LOGFILE" &
            elif command -v kate &> /dev/null; then
                kate "$LOGFILE" &
            else
                less "$LOGFILE"
            fi
        else
            if command -v xdg-open &> /dev/null; then
                xdg-open "$LOGDIR"
            else
                echo "  Журнал: $LOGDIR"
            fi
        fi
    fi

    show_menu
}

#############################################################
#  Open project folder
#############################################################
open_folder() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        open "$PROJECT"
    else
        if command -v xdg-open &> /dev/null; then
            xdg-open "$PROJECT"
        else
            echo "  Папка проекта: $PROJECT"
            read -p "  Нажми Enter..."
        fi
    fi
    show_menu
}

#############################################################
#  Show help
#############################################################
show_help() {
    echo ""
    echo "  Dahua Exporter - launcher"
    echo ""
    echo "  Без аргументов открывается меню."
    echo ""
    echo "  Аргументы:"
    echo "    run      запустить программу сразу"
    echo "    setup    установить зависимости"
    echo "    diag     диагностика"
    echo "    logs     журнал"
    echo ""
    exit 0
}

#############################################################
#  No Python error
#############################################################
no_python() {
    clear
    echo ""
    echo -e "${RED}  ==========================================${NC}"
    echo -e "${RED}    Python не найден${NC}"
    echo -e "${RED}  ==========================================${NC}"
    echo ""
    echo "  Установи Python 3.10 или новее:"
    echo ""
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "    brew install python@3.11"
        echo ""
        echo "  Если Homebrew не установлен:"
        echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    else
        echo "    sudo apt install python3 python3-pip    # Debian/Ubuntu"
        echo "    sudo dnf install python3 python3-pip    # Fedora"
        echo "    sudo pacman -S python python-pip        # Arch"
    fi
    echo ""
    read -p "  Нажми Enter..."
    exit 1
}

#############################################################
#  Main entry point
#############################################################

# Find Python first
if ! find_python; then
    no_python
fi

# Parse arguments
case "${1:-}" in
    run)    ensure_deps ;;
    setup)  install_deps ;;
    diag)   run_diag ;;
    logs)   open_logs ;;
    --help|-h) show_help ;;
    *)      show_menu ;;
esac
