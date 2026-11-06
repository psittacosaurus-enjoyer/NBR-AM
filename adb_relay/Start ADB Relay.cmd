@echo off
rem Starts the standalone NBR ADB Relay in its own minimized window.
cd /d "%~dp0"
set "RELAY_PY="
where py >nul 2>nul && set "RELAY_PY=py -3"
if not defined RELAY_PY where python >nul 2>nul && set "RELAY_PY=python"
if not defined RELAY_PY if exist "%~dp0..\.venv\Scripts\python.exe" set RELAY_PY="%~dp0..\.venv\Scripts\python.exe"
if not defined RELAY_PY (
  echo Python 3 was not found. Install Python 3 or create ..\.venv.
  pause
  exit /b 1
)
start "NBR ADB Relay" /min %RELAY_PY% "%~dp0relay.py"
