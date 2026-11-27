@echo off
REM Start the portal for local network (LAN) testing. Double-click this file or run it in Command Prompt.
REM It does not need "python" on PATH: run_lan.ps1 finds a Python interpreter with Streamlit installed.
REM Options are passed through, e.g.:  run_lan.bat -Port 8502   or   run_lan.bat -Setup   or   run_lan.bat -CheckOnly
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where powershell >nul 2>nul
if errorlevel 1 goto fallback
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_lan.ps1" %*
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
    echo.
    echo The launcher stopped with exit code %ERR%. Read the messages above.
    pause
)
exit /b %ERR%

:fallback
echo PowerShell was not found; trying the most common Python locations directly...
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo No Python interpreter was found. Install Python from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH", then run this file again.
    pause
    exit /b 1
)
echo Using %PY%
%PY% -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
pause
