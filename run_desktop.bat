@echo off
REM Start the Local Desktop UI (course setup, rosters, AI grading, review, backups, archives, billing).
REM It is bound to 127.0.0.1, so only this computer can open it. Python is found automatically.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_lan.ps1" -App desktop_app.py -Address 127.0.0.1 -Port 8502 %*
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
    echo.
    echo The launcher stopped with exit code %ERR%. Read the messages above.
    pause
)
exit /b %ERR%
