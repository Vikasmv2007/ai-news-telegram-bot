@echo off
REM Starts the bot on Windows. Double-click or run from Task Scheduler.
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo Virtual environment missing. Run scripts\setup_windows.bat first.
  pause
  exit /b 1
)
:loop
.venv\Scripts\python.exe -m app.main
echo Bot stopped (exit code %errorlevel%). Restarting in 15 seconds... (close this window to stop)
timeout /t 15 /nobreak >nul
goto loop
