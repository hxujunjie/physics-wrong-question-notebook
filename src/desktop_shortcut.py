"""Create a Windows desktop shortcut for one-click teacher launch."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "物理错题整理-教师端.lnk"
SHORTCUT_DESCRIPTION = "一键启动物理错题整理教师端：识别 → 确认不确定项 → 生成错题集"


def desktop_dir() -> Path:
    """Prefer the real Desktop folder (handles OneDrive redirection)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_DESKTOPDIRECTORY = 0x10
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop"


def shortcut_path() -> Path:
    return desktop_dir() / SHORTCUT_NAME


def resolve_launch_target(root: Path | None = None) -> tuple[Path, str, Path]:
    """Return (target_path, arguments, working_dir) for the teacher launcher.

    Priority:
    1. Frozen PyInstaller exe (current process)
    2. 一键启动教师端.vbs (no console window)
    3. pythonw + launch_teacher.pyw
    4. 一键启动教师端.bat
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return exe, "", exe.parent

    base = Path(root).resolve() if root else Path(__file__).resolve().parents[1]
    vbs = base / "一键启动教师端.vbs"
    if vbs.is_file():
        return vbs, "", base

    pythonw = base / ".venv" / "Scripts" / "pythonw.exe"
    launcher = base / "launch_teacher.pyw"
    if pythonw.is_file() and launcher.is_file():
        return pythonw, f'"{launcher}"', base

    bat = base / "一键启动教师端.bat"
    if bat.is_file():
        return bat, "", base

    raise FileNotFoundError("找不到教师端启动入口（exe / vbs / launch_teacher.pyw / bat）")


def create_desktop_shortcut(*, root: Path | None = None, force: bool = False) -> Path:
    """Create or refresh the desktop shortcut. Returns the .lnk path."""
    if os.name != "nt":
        raise RuntimeError("桌面快捷方式仅支持 Windows")

    target, arguments, workdir = resolve_launch_target(root)
    link = shortcut_path()
    if link.is_file() and not force:
        return link

    link.parent.mkdir(parents=True, exist_ok=True)
    # PowerShell + WScript.Shell is available on stock Windows; no extra package needed.
    ps = f"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut({_ps_quote(str(link))})
$shortcut.TargetPath = {_ps_quote(str(target))}
$shortcut.Arguments = {_ps_quote(arguments)}
$shortcut.WorkingDirectory = {_ps_quote(str(workdir))}
$shortcut.Description = {_ps_quote(SHORTCUT_DESCRIPTION)}
$shortcut.WindowStyle = 1
if ([System.IO.File]::Exists({_ps_quote(str(target))}) -and ({_ps_quote(target.suffix.lower())} -eq '.exe')) {{
  $shortcut.IconLocation = {_ps_quote(str(target) + ",0")}
}}
$shortcut.Save()
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0 or not link.is_file():
        detail = (completed.stderr or completed.stdout or "未知错误").strip()
        raise RuntimeError(f"创建桌面快捷方式失败：{detail[-800:]}")
    return link


def ensure_desktop_shortcut(*, root: Path | None = None) -> Path | None:
    """Best-effort create; never raise to callers (startup must not fail because of shortcuts)."""
    try:
        return create_desktop_shortcut(root=root, force=False)
    except Exception:
        return None


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    force = "--force" in sys.argv
    path = create_desktop_shortcut(force=force)
    print(path)
