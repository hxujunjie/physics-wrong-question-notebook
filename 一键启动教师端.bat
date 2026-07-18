@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHON="
for %%V in (3.13 3.12 3.11) do (
  if not defined PYTHON (
    py -%%V -c "import sys" >nul 2>nul && set "PYTHON=py -%%V"
  )
)
if not defined PYTHON goto no_python
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; assert (3,11)<=sys.version_info[:2]<=(3,13)" >nul 2>nul || goto bad_venv
)
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment...
  call %PYTHON% -m venv .venv || goto failed
)
echo [2/4] Checking locked dependencies...
".venv\Scripts\python.exe" -c "import importlib.metadata as m; expected={'opencv-python':'5.0.0.93','PyMuPDF':'1.28.0','numpy':'2.5.1','Pillow':'11.3.0','scikit-image':'0.26.0','rapidocr-onnxruntime':'1.2.3','onnxruntime':'1.27.0','openai':'2.45.0'}; bad=[n for n,v in expected.items() if m.version(n)!=v]; import fitz,numpy,PIL,cv2,skimage,rapidocr_onnxruntime,onnxruntime,openai; assert not bad,bad" >nul 2>nul || goto install
goto start
:install
echo [3/4] Installing locked dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade --force-reinstall -r requirements-browser.lock.txt || goto failed
:start
echo [4/4] Starting local teacher server and opening the browser...
echo        Flow: recognize, confirm uncertain items, then export wrong-book PDF
if not exist "launch_teacher.pyw" goto missing_launcher
if not exist "src\recognition_pipeline.py" goto missing_code
if not exist "src\recognition_import.py" goto missing_code
if not exist ".venv\Scripts\pythonw.exe" goto failed
start "" ".venv\Scripts\pythonw.exe" "launch_teacher.pyw"
if not errorlevel 1 exit /b 0
:failed
echo [ERROR] Startup failed. Review the messages above.
echo You can also run 维护工具\重新安装依赖.bat
pause
exit /b 1
:missing_launcher
echo [ERROR] Missing launch_teacher.pyw
pause
exit /b 1
:missing_code
echo [ERROR] Missing recognition_pipeline / recognition_import. Update the project code.
pause
exit /b 1
:no_python
echo [ERROR] Python 3.11 through 3.13 was not found.
pause
exit /b 1
:bad_venv
echo [ERROR] The existing .venv uses an unsupported Python version.
echo Remove .venv and run this launcher again.
pause
exit /b 1
