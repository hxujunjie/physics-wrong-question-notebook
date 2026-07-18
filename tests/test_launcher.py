import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("launch_teacher", str(ROOT / "launch_teacher.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)


class FakeResponse:
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return (b'{"status":"ok","app_version":"1.6.1",'
                            b'"pipeline_version":"teacher-review-6-confirm-then-export"}')


class LauncherTests(unittest.TestCase):
    def test_live_url_uses_recorded_dynamic_port(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({"port": 8772}), encoding="utf-8")
            with patch.object(launcher, "urlopen", return_value=FakeResponse()) as request:
                self.assertEqual(launcher.live_url(state), "http://127.0.0.1:8772/")
            self.assertIn("127.0.0.1:8772/api/health", request.call_args.args[0])

    def test_stale_runtime_record_can_be_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"; state.write_text("{}", encoding="utf-8")
            self.assertIsNone(launcher.live_url(state, cleanup_stale=True))
            self.assertFalse(state.exists())

    def test_outdated_server_is_not_reused(self):
        class OldResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self):
                return (b'{"status":"ok","app_version":"1.6.0",'
                        b'"pipeline_version":"teacher-review-5-ai-clean-pdf-only"}')

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({"port": 8765, "token": "tok"}), encoding="utf-8")
            with patch.object(launcher, "urlopen", return_value=OldResponse()):
                self.assertIsNone(launcher.live_url(state))

    def test_ensure_current_server_slot_stops_outdated(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({"port": 8765, "token": "tok"}), encoding="utf-8")
            log = Path(directory) / "startup.log"
            health = {"status": "ok", "app_version": "1.6.0", "pipeline_version": "old"}
            with patch.object(launcher, "LOG", log), \
                 patch.object(launcher, "_read_health", side_effect=[("http://127.0.0.1:8765/", health), None]), \
                 patch.object(launcher, "stop_running_server", return_value=True) as stop:
                launcher.ensure_current_server_slot(state)
            stop.assert_called_once()

    def test_missing_virtual_environment_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "虚拟环境不存在"):
                launcher.validate_runtime(Path(directory))

    def test_second_launch_reuses_existing_service(self):
        with patch.object(launcher, "ensure_current_server_slot"), \
             patch.object(launcher, "live_url", return_value="http://127.0.0.1:8765/"), \
             patch.object(launcher, "open_page", return_value=True) as opened, \
             patch.object(launcher, "acquire_lock") as lock:
            self.assertEqual(launcher.main(), 0)
            opened.assert_called_once_with("http://127.0.0.1:8765/")
            lock.assert_not_called()

    def test_browser_failure_shows_actual_url(self):
        with patch.object(launcher.webbrowser, "open", return_value=False), \
             patch.object(launcher, "message") as shown:
            self.assertFalse(launcher.open_page("http://127.0.0.1:8766/"))
            self.assertIn("127.0.0.1:8766", shown.call_args.args[1])


if __name__ == "__main__": unittest.main()
