"""Windows one-click launcher for the local teacher browser application."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "_runtime"
STATE = RUNTIME / "web_server.json"
LOCK = RUNTIME / "launcher.lock"
LOG = RUNTIME / "startup.log"
START_TIMEOUT_SECONDS = 30
# Keep in sync with src.web_teacher.APP_VERSION / src.teacher_pipeline.PIPELINE_VERSION
EXPECTED_APP_VERSION = "1.6.1"
EXPECTED_PIPELINE_VERSION = "teacher-review-6-confirm-then-export"


def message(title: str, body: str, error: bool = False) -> None:
    flags = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(None, body, title, flags)


def _read_health(state_path: Path = STATE) -> tuple[str, dict] | None:
    """Return (url, health) for a reachable local teacher server, if any."""
    if not state_path.is_file():
        return None
    try:
        info = json.loads(state_path.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{int(info['port'])}/"
        with urlopen(url + "api/health", timeout=1.5) as response:
            health = json.loads(response.read().decode("utf-8"))
        if health.get("status") != "ok":
            return None
        return url, health
    except Exception:
        return None


def _version_matches(health: dict) -> bool:
    return (
        health.get("app_version") == EXPECTED_APP_VERSION
        and health.get("pipeline_version") == EXPECTED_PIPELINE_VERSION
    )


def live_url(state_path: Path = STATE, *, cleanup_stale: bool = False) -> str | None:
    """Return URL only for a healthy server that matches this launcher version."""
    found = _read_health(state_path)
    if found is None:
        if cleanup_stale and state_path.is_file():
            # Keep the record when a live but outdated server still owns the port;
            # stop_outdated_server() needs the token to shut it down cleanly.
            try:
                info = json.loads(state_path.read_text(encoding="utf-8"))
                url = f"http://127.0.0.1:{int(info['port'])}/"
                with urlopen(url + "api/health", timeout=1.0) as response:
                    health = json.loads(response.read().decode("utf-8"))
                if health.get("status") == "ok":
                    return None
            except Exception:
                pass
            state_path.unlink(missing_ok=True)
        return None
    url, health = found
    if not _version_matches(health):
        return None
    return url


def stop_running_server(state_path: Path = STATE, *, timeout: float = 8.0) -> bool:
    """Ask the recorded local server to shut down. Returns True if it went away."""
    if not state_path.is_file():
        return True
    try:
        info = json.loads(state_path.read_text(encoding="utf-8"))
        port = int(info["port"])
        token = str(info.get("token") or "")
    except Exception:
        state_path.unlink(missing_ok=True)
        return True

    from urllib.request import Request

    if token:
        req = Request(
            f"http://127.0.0.1:{port}/api/shutdown",
            method="POST",
            headers={"X-Review-Token": token, "Content-Type": "application/json"},
            data=b"{}",
        )
        try:
            urlopen(req, timeout=3).read()
        except Exception:
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _read_health(state_path) is None:
            state_path.unlink(missing_ok=True)
            return True
        time.sleep(0.2)
    return _read_health(state_path) is None


def ensure_current_server_slot(state_path: Path = STATE) -> None:
    """If an older teacher server is still running, stop it so this version can start."""
    found = _read_health(state_path)
    if found is None:
        # Stale runtime file with no live process.
        if state_path.is_file():
            try:
                json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                state_path.unlink(missing_ok=True)
        return
    _url, health = found
    if _version_matches(health):
        return
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] stopping outdated server "
            f"app={health.get('app_version')!r} pipeline={health.get('pipeline_version')!r}\n"
        )
    if not stop_running_server(state_path):
        raise RuntimeError(
            "检测到旧版教师端仍在运行，且未能自动停止。\n"
            "请双击「维护工具\\停止教师端.bat」或页面内「退出服务」后重试。"
        )


def open_page(url: str) -> bool:
    try:
        opened = bool(webbrowser.open(url, new=2))
    except Exception:
        opened = False
    if not opened:
        message("物理错题整理", f"服务已经启动，但浏览器未能自动打开。\n\n请复制并访问：\n{url}")
    return opened


def log_tail(path: Path = LOG, limit: int = 3000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-limit:].strip()
    except OSError:
        return ""


def runtime_paths(root: Path = ROOT) -> tuple[Path, Path]:
    scripts = root / ".venv" / "Scripts"
    return scripts / "python.exe", scripts / "pythonw.exe"


def validate_runtime(root: Path = ROOT) -> tuple[Path, Path]:
    python, pythonw = runtime_paths(root)
    if not python.is_file() or not pythonw.is_file():
        raise RuntimeError("Python 虚拟环境不存在。请先双击项目中的“一键启动教师端.bat”完成首次安装。")
    check = subprocess.run(
        [str(python), "-c", "from src.web_teacher import make_server"],
        cwd=str(root), capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if check.returncode:
        detail = (check.stderr or check.stdout or "依赖检查失败").strip()
        raise RuntimeError(f"运行依赖不完整或无法加载：\n{detail[-1600:]}")
    return python, pythonw


def acquire_lock(lock_path: Path = LOCK) -> int | None:
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


def wait_for_existing(timeout: float = 12) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = live_url(cleanup_stale=False)
        if url:
            return url
        time.sleep(0.25)
    return None


def start_server(root: Path = ROOT) -> str:
    _python, pythonw = validate_runtime(root)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] launching teacher server\n")
        stream.flush()
        process = subprocess.Popen(
            [str(pythonw), "-u", str(root / "run_web_teacher.py"), "--serve-only"],
            cwd=str(root), stdout=stream, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        url = live_url(cleanup_stale=False)
        if url:
            return url
        if process.poll() is not None:
            raise RuntimeError(f"本地服务启动后立即退出。\n\n{log_tail() or '请查看启动日志。'}")
        time.sleep(0.25)
    try:
        process.terminate()
    except OSError:
        pass
    raise RuntimeError(f"等待本地服务启动超时。\n\n{log_tail() or '请查看启动日志。'}")


def ensure_shortcut() -> None:
    """Put a one-click icon on the teacher desktop if missing."""
    try:
        sys.path.insert(0, str(ROOT))
        from src.desktop_shortcut import ensure_desktop_shortcut

        ensure_desktop_shortcut(root=ROOT)
    except Exception:
        pass


def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    ensure_shortcut()
    try:
        # After code updates, an old process may still hold 8765. Stop it first.
        ensure_current_server_slot()
    except Exception as exc:
        message("物理错题整理启动失败", f"{exc}\n\n启动日志：\n{LOG}", error=True)
        return 1

    url = live_url(cleanup_stale=True)
    if url:
        open_page(url)
        return 0
    lock_handle = acquire_lock()
    if lock_handle is None:
        url = wait_for_existing()
        if url:
            open_page(url)
            return 0
        LOCK.unlink(missing_ok=True)
        lock_handle = acquire_lock()
        if lock_handle is None:
            message("物理错题整理", "另一个启动任务仍在运行，请稍后重试。", error=True)
            return 1
    try:
        os.close(lock_handle)
        # Re-check after waiting for the lock: another launcher may have started
        # the current version, or an outdated one may have reappeared.
        ensure_current_server_slot()
        url = live_url(cleanup_stale=True)
        if not url:
            url = start_server()
        open_page(url)
        return 0
    except Exception as exc:
        detail = str(exc)
        try:
            with LOG.open("a", encoding="utf-8") as stream:
                stream.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] launcher error: {detail}\n")
        except OSError:
            pass
        message("物理错题整理启动失败", f"{detail}\n\n启动日志：\n{LOG}", error=True)
        return 1
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
