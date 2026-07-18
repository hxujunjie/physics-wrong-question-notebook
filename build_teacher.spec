# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
rapidocr_data = collect_data_files("rapidocr_onnxruntime")
rapidocr_hiddenimports = collect_submodules("rapidocr_onnxruntime")
cv2_data = collect_data_files("cv2", include_py_files=True)

a = Analysis(
    ["run_web_teacher.py"],
    pathex=["."],
    binaries=[],
    datas=[("src/", "src/"), ("web/", "web/")] + rapidocr_data + cv2_data,
    hiddenimports=["cv2", "numpy", "fitz", "PIL", "PIL.Image", "rapidocr_onnxruntime", "openai", "httpx", "httpcore"] + rapidocr_hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="physics-wrong-book-teacher", debug=False, bootloader_ignore_signals=False, strip=False, upx=True, console=False)
COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=True, name="physics-wrong-book-teacher")
