@echo off
setlocal
cd /d "%~dp0"
set "PY="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,12),(3,13)) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto install
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,12),(3,13)) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto install
echo [ERROR] Python 3.11, 3.12, or 3.13 is required.
pause
exit /b 1
:install
%PY% -m venv .venv
if errorlevel 1 goto failed_venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed_pip
".venv\Scripts\python.exe" -m pip install -r requirements-browser.lock.txt
if errorlevel 1 goto failed_deps
".venv\Scripts\python.exe" -c "import cv2,fitz,numpy,PIL,skimage,rapidocr_onnxruntime,onnxruntime; print('Dependency import check passed.')"
if errorlevel 1 goto failed_import
echo Installation completed. Run the teacher startup BAT next time.
pause
exit /b 0
:failed_venv
echo [ERROR] Could not create .venv.
goto failed
:failed_pip
echo [ERROR] Could not upgrade pip.
goto failed
:failed_deps
echo [ERROR] Could not install dependencies.
goto failed
:failed_import
echo [ERROR] Dependency import check failed.
:failed
pause
exit /b 1
