@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" (
    echo The project Python environment is missing. See README.md for setup.
    pause
    exit /b 1
)
set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -c "from PySide6 import QtWidgets; import manager_gui"
if errorlevel 1 (
    echo NBR Automation Manager could not load. Run scripts\Setup NBR Manager.cmd.
    pause
    exit /b 1
)
start "" /d "%CD%" ".venv\Scripts\pythonw.exe" "src\manager_gui.py"
exit /b 0
