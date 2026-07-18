"""Start or stop the local-only teacher browser server."""
from __future__ import annotations
import json, sys
from pathlib import Path
from urllib.request import Request, urlopen

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
ASSET_ROOT = Path(getattr(sys, "_MEIPASS", ROOT)) if FROZEN else ROOT
sys.path.insert(0,str(ROOT))
from src.web_teacher import make_server

STATE = ROOT / "_runtime" / "web_server.json"


def _health():
    """Return (url, health) for a live server, or None."""
    if not STATE.exists():
        return None
    try:
        info = json.loads(STATE.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{int(info['port'])}/"
        with urlopen(url + "api/health", timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        if health.get("status") != "ok":
            return None
        return url, health
    except Exception:
        return None


def running_url(*, require_current_version: bool = True):
    """Return the live server URL, cleaning only a stale runtime record.

    When require_current_version is True (default for normal start), an outdated
    process is treated as not usable so the caller can stop and relaunch.
    """
    from src.teacher_pipeline import PIPELINE_VERSION
    from src.web_teacher import APP_VERSION

    found = _health()
    if found is None:
        if STATE.exists():
            # Only delete the record when nothing is actually listening.
            try:
                info = json.loads(STATE.read_text(encoding="utf-8"))
                urlopen(f"http://127.0.0.1:{int(info['port'])}/api/health", timeout=1).read()
            except Exception:
                STATE.unlink(missing_ok=True)
        return None
    url, health = found
    if require_current_version and (
        health.get("app_version") != APP_VERSION
        or health.get("pipeline_version") != PIPELINE_VERSION
    ):
        return None
    return url


def stop():
    if not STATE.exists():
        print("服务未运行（找不到运行状态文件）。")
        return 1
    info = json.loads(STATE.read_text(encoding="utf-8"))
    req = Request(
        f"http://127.0.0.1:{info['port']}/api/shutdown",
        method="POST",
        headers={"X-Review-Token": info["token"], "Content-Type": "application/json"},
        data=b"{}",
    )
    try:
        urlopen(req, timeout=5).read()
        print("已请求停止教师端服务。")
        return 0
    except Exception as exc:
        print(f"停止失败: {exc}")
        return 1
def ensure_shortcut():
    """Create desktop one-click shortcut for teachers (best-effort)."""
    if "--serve-only" in sys.argv or "--stop" in sys.argv:
        return
    try:
        from src.desktop_shortcut import ensure_desktop_shortcut
        ensure_desktop_shortcut(root=ROOT)
    except Exception:
        pass


def main():
    if "--stop" in sys.argv:
        return stop()
    if "--create-shortcut" in sys.argv:
        from src.desktop_shortcut import create_desktop_shortcut
        path = create_desktop_shortcut(root=ROOT, force=True)
        print(f"已创建桌面快捷方式：{path}")
        return 0
    serve_only = "--serve-only" in sys.argv
    ensure_shortcut()
    url = running_url(require_current_version=True)
    if url:
        print(f"教师端服务已经在运行：{url}", flush=True)
        if not serve_only:
            import webbrowser
            webbrowser.open(url)
        return 0

    # Outdated process may still own the port; stop it before binding.
    outdated = _health()
    if outdated is not None:
        print("检测到旧版教师端仍在运行，正在停止…", flush=True)
        stop()
        import time
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and _health() is not None:
            time.sleep(0.2)
        if _health() is not None:
            print("无法停止旧版教师端，请先运行 维护工具\\停止教师端.bat", flush=True)
            return 1
        STATE.unlink(missing_ok=True)

    server = make_server(ROOT, web_root=ASSET_ROOT / "web")
    server.write_runtime()
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"教师端浏览器版已启动：{url}", flush=True)
    if not serve_only:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.runtime_path.unlink(missing_ok=True)
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
