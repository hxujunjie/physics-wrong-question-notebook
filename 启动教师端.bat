@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto start
echo [ERROR] Python environment is missing.
echo Please run 一键启动教师端.bat first.
pause
exit /b 1
:start
if not exist "launch_teacher.pyw" (
  echo [ERROR] Missing launch_teacher.pyw
  pause
  exit /b 1
)
if not exist "src\recognition_pipeline.py" (
  echo [ERROR] Missing recognition_pipeline. Update the project code first.
  pause
  exit /b 1
)
if not exist "src\recognition_import.py" (
  echo [ERROR] Missing recognition_import. Update the project code first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "launch_teacher.pyw"
if not errorlevel 1 exit /b 0
echo [ERROR] The teacher server exited unexpectedly.
echo Try 维护工具\停止教师端.bat then relaunch, or 维护工具\重新安装依赖.bat
pause
exit /b 1
