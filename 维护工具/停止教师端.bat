@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" goto missing
.venv\Scripts\python.exe run_web_teacher.py --stop
if not errorlevel 1 (
  echo Teacher server stop requested.
  exit /b 0
)
echo [INFO] No active local teacher server was found.
pause
exit /b 1
:missing
echo [ERROR] Python environment is missing.
pause
exit /b 1
