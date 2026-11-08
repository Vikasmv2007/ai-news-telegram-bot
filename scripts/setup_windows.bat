@echo off
REM One-time setup on Windows: creates .venv and installs dependencies.
cd /d "%~dp0.."
py -3 -m venv .venv || python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if not exist .env copy .env.example .env
echo.
echo Setup done. Now edit .env (notepad .env) and then run scripts\run_bot.bat
pause
