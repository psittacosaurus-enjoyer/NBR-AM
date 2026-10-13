@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo The existing NBR project Python environment is missing.
    echo Restore the environment as described in MANAGER.md, then run this setup again.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-gui.txt"
if errorlevel 1 (
    echo.
    echo Setup failed. Review the error above.
    pause
    exit /b 1
)
echo.
echo Setup complete. Double-click Launch NBR Manager.cmd.
pause
