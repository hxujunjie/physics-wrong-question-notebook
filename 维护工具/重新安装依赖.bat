@echo off
setlocal
cd /d "%~dp0\.."
where py >/dev/null 2>/dev/null || goto failed
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv || goto failed
.venv\Scripts\python.exe -m pip install --disable-pip-version-check --upgrade --force-reinstall -r requirements-browser.lock.txt || goto failed
echo [OK] Dependencies reinstalled from requirements-browser.lock.txt
echo Next: double-click 一键启动教师端.bat
pause
exit /b 0
:failed
echo [ERROR] Dependency repair failed.
pause
exit /b 1
