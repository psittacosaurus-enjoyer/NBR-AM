@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo The project Python environment is missing. See MANAGER.md for setup.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -c "from PySide6 import QtWidgets; import manager_gui"
if errorlevel 1 (
    echo.
    echo NBR Automation Manager could not load. Run Setup NBR Manager.cmd.
    echo If setup is already complete, see the error above.
    pause
    exit /b 1
)
start "" /d "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0manager_gui.py"
exit /b 0
