import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import fitz

from src import ai_settings, recognition_import, review_workspace
from src.web_teacher import WebTeacherServer, _is_client_disconnect


class WebTeacherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "照片"
        (self.project / "学生甲").mkdir(parents=True)
        self.pdf = self.root / "练习册.pdf"
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "9. clean question")
        doc.save(self.pdf)
        doc.close()
        import cv2
        import numpy as np

        cv2.imencode(".jpg", np.full((80, 100, 3), 255, dtype=np.uint8))[1].tofile(str(self.project / "学生甲" / "a.jpg"))
        self.payload = {
            "schema_version": "1.0",
            "images": [
                {
                    "student_name": "学生甲",
                    "photo_file": str(self.project / "学生甲" / "a.jpg"),
                    "matched_reference_file": str(self.pdf),
                    "pdf_page": 1,
                    "page_match_confidence": 0.95,
                    "visible_questions": [
                        {
                            "question_no": "9",
                            "photo_bbox": [0.1, 0.1, 0.8, 0.8],
                            "reference_bbox": None,
                            "status": "wrong",
                            "number_confidence": 0.95,
                            "status_confidence": 0.95,
                            "evidence": "红叉",
                        }
                    ],
                    "needs_manual_review": False,
                }
            ],
        }
        self.json = self.root / "recognition_result.json"
        self.json.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        self.output = self.root / "output"
        self.settings = self.root / "settings.json"
        self.settings_patch = patch.object(ai_settings, "settings_path", return_value=self.settings)
        self.settings_patch.start()
        self.server = WebTeacherServer(self.root, 0)
        self.server.web_root = Path(__file__).resolve().parents[1] / "web"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.settings_patch.stop()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, token=None):
        req = Request(
            self.base + path,
            method=method,
            headers={"Content-Type": "application/json", "X-Review-Token": token or self.server.token},
            data=json.dumps(body).encode() if body is not None else None,
        )
        return urlopen(req, timeout=5)

    def configure(self, provider="dashscope", model="qwen-vl-plus"):
        return json.loads(
            self.request(
                "/api/settings/provider",
                "POST",
                {"api_key": "xai-test-key-123456789", "provider": provider, "model": model},
            ).read()
        )

    def test_settings_and_preflight_scan_student_folders(self):
        settings = self.configure()
        self.assertTrue(settings["configured"])
        self.assertEqual(settings["provider"], "dashscope")
        self.assertNotIn("xai-test", self.settings.read_text(encoding="utf-8"))
        data = json.loads(
            self.request(
                "/api/preflight",
                "POST",
                {"clean_pdf": str(self.pdf), "photo_root": str(self.project), "output_root": str(self.root / "结果")},
            ).read()
        )
        self.assertEqual(data["photo_count"], 1)
        self.assertEqual(data["pdf_page_count"], 1)
        self.assertEqual(data["students"][0]["student"], "学生甲")
        self.assertEqual(data["provider"], "dashscope")

    def test_batch_creates_an_isolated_output_task(self):
        self.configure()
        with patch.object(self.server, "start_recognition", return_value=True) as started:
            response = self.request(
                "/api/batch",
                "POST",
                {"clean_pdf": str(self.pdf), "photo_root": str(self.project), "output_root": str(self.root / "结果")},
            )
        self.assertEqual(response.status, 202)
        config = started.call_args.args[0]
        self.assertTrue(Path(config["output_dir"]).is_dir())
        self.assertEqual(config["students"][0]["student"], "学生甲")
        self.assertEqual(config["provider"], "dashscope")

    def test_import_recognition_without_api_key(self):
        data = json.loads(
            self.request(
                "/api/import-recognition",
                "POST",
                {
                    "recognition_json": str(self.json),
                    "clean_pdf": str(self.pdf),
                    "output_root": str(self.root / "导入输出"),
                },
            ).read()
        )
        self.assertIn(data["status"], {"success", "partial"})
        self.assertTrue(Path(data["output_dir"]).is_dir())
        from urllib.parse import quote

        students = json.loads(
            self.request(f"/api/students?output_dir={quote(data['output_dir'])}").read()
        )["students"]
        self.assertEqual(students[0]["student"], "学生甲")

    def test_manual_crop_endpoint_records_teacher_source(self):
        recognition_import.import_recognition_result(self.json, self.pdf, self.output)
        manifest = review_workspace.load_manifest(next((self.output / "_cache").glob("*/review_manifest.json")))
        q = manifest["photo_tasks"][0]["page_review_questions"][0]
        data = json.loads(
            self.request(
                "/api/manual-crop",
                "POST",
                {
                    "output_dir": str(self.output),
                    "student": manifest["student"],
                    "evidence_id": q["evidence_id"],
                    "manual_segments": [{"page_index": 0, "bbox_norm": [0.05, 0.05, 0.95, 0.95]}],
                },
            ).read()
        )
        self.assertEqual(data["question"]["crop_source"], "原始PDF")
        self.assertIn("page", data)
        self.assertIn("synced_count", data)

    def test_invalid_token_is_rejected(self):
        with self.assertRaises(Exception):
            self.request("/api/preflight", "POST", {}, token="bad")

    def test_client_disconnect_errors_are_detected(self):
        self.assertTrue(_is_client_disconnect(ConnectionAbortedError(10053, "aborted")))
        self.assertTrue(_is_client_disconnect(BrokenPipeError()))
        self.assertFalse(_is_client_disconnect(ValueError("bad input")))

    def test_post_value_error_returns_json_400(self):
        from urllib.error import HTTPError

        try:
            self.request("/api/preflight", "POST", {"clean_pdf": "", "photo_root": ""})
            self.fail("expected HTTPError")
        except HTTPError as exc:
            self.assertEqual(exc.code, 400)
            body = json.loads(exc.read().decode("utf-8"))
            self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()

