@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r "requirements.txt" -r "requirements-gui.txt"
if errorlevel 1 (
    echo Setup failed. Review the error above.
    pause
    exit /b 1
)
echo Setup complete. Double-click scripts\Launch NBR Manager.cmd.
pause
