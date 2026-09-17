#!/bin/bash
#############################################################
#  Dahua Exporter - quick launch for macOS
#
#  Double-click this file to start the program.
#  The Terminal window will open and close automatically.
#############################################################

cd "$(dirname "$0")"

# Find Python
if command -v python3 &> /dev/null; then
    PY="python3"
elif command -v python &> /dev/null; then
    PY="python"
else
    osascript -e 'display alert "Python не найден" message "Установи Python 3.10 или новее:\n\nbrew install python@3.11" as critical'
    exit 1
fi

# Check dependencies quickly
if ! $PY -c "import PySide6, requests, keyring" &> /dev/null; then
    # Dependencies missing - open the full launcher menu
    exec bash ./start.sh
    exit 0
fi

# Clean cache
rm -rf src/dahua_exporter/__pycache__ 2>/dev/null

# Launch
$PY -c "import sys; sys.path.insert(0, 'src'); from dahua_exporter.gui import main; main()"
