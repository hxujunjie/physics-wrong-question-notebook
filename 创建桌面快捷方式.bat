@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m src.desktop_shortcut --force
  if errorlevel 1 goto fail
  echo.
  echo Desktop shortcut created: 物理错题整理-教师端
  echo Flow: recognize, confirm uncertain items, then export wrong-book PDF
  pause
  exit /b 0
)

if exist "physics-wrong-book-teacher.exe" (
  "physics-wrong-book-teacher.exe" --create-shortcut
  if errorlevel 1 goto fail
  echo.
  echo Desktop shortcut created.
  pause
  exit /b 0
)

if exist "dist\physics-wrong-book-teacher\physics-wrong-book-teacher.exe" (
  "dist\physics-wrong-book-teacher\physics-wrong-book-teacher.exe" --create-shortcut
  if errorlevel 1 goto fail
  echo.
  echo Desktop shortcut created.
  pause
  exit /b 0
)

echo [ERROR] Cannot find Python venv or packaged teacher exe.
echo Run 一键启动教师端.bat once first, or place this file next to the exe.
pause
exit /b 1

:fail
echo [ERROR] Failed to create desktop shortcut.
pause
exit /b 1
