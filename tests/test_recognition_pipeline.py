import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import fitz
import numpy as np

from src import recognition_pipeline, review_workspace
from src.ai_client import AiReply


class FakeAiClient:
    def __init__(self, *_args, **_kwargs):
        self.model = _kwargs.get("model") or (_args[1] if len(_args) > 1 else "qwen-vl-plus")
        self.provider = _kwargs.get("provider") or "dashscope"
        self.base_url = _kwargs.get("base_url") or (_args[2] if len(_args) > 2 else "https://example.com/v1")

    def index_pdf_pages(self, pages):
        return AiReply(
            {
                "pages": [
                    {
                        "page_number": number,
                        "anchor_text": "第9题 力学",
                        "questions": [{"question_no": "9", "bbox": [0.1, 0.1, 0.9, 0.8]}],
                    }
                    for number, _ in pages
                ]
            },
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        )

    def inspect_photo(self, _photo):
        return AiReply(
            {
                "page_anchor": "第9题 力学",
                "needs_manual_review": False,
                "review_reason": "",
                "visible_questions": [
                    {
                        "question_no": "9",
                        "photo_bbox": [0.1, 0.1, 0.9, 0.8],
                        "status": "wrong",
                        "number_confidence": 0.95,
                        "status_confidence": 0.92,
                        "evidence": "老师红叉",
                    }
                ],
            },
            {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
        )

    def verify_page(self, _photo, candidates):
        return AiReply(
            {"pdf_page": candidates[0][0], "confidence": 0.95},
            {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
        )


class RecognitionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.photos = self.root / "照片" / "学生甲"
        self.photos.mkdir(parents=True)
        encoded = cv2.imencode(".jpg", np.full((120, 180, 3), 255, dtype=np.uint8))[1]
        encoded.tofile(str(self.photos / "作业.jpg"))
        self.pdf = self.root / "练习册.pdf"
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "第9题 力学")
        doc.save(self.pdf)
        doc.close()
        self.output = self.root / "输出"
        self.output.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def config(self, budget=10):
        return {
            "clean_pdf": str(self.pdf),
            "photo_root": str(self.photos.parent),
            "output_root": str(self.root),
            "output_dir": str(self.output),
            "students": [{"student": "学生甲", "photos": [str(self.photos / "作业.jpg")]}],
            "provider": "dashscope",
            "model": "qwen-vl-plus",
            "base_url": "https://example.com/v1",
            "api_key": "test-key-12345678",
            "base_calls": 2,
            "call_budget": budget,
        }

    def test_preflight_discovers_students_and_budget(self):
        with patch.object(recognition_pipeline, "cache_root", return_value=self.root / "cache"), patch.object(
            recognition_pipeline.ai_settings, "summary", return_value={"provider": "dashscope", "model": "qwen-vl-plus", "effective_base_url": "https://example.com/v1"}
        ):
            result = recognition_pipeline.preflight(self.pdf, self.photos.parent, self.root / "结果")
        self.assertEqual(result["photo_count"], 1)
        self.assertEqual(result["students"][0]["student"], "学生甲")
        self.assertGreaterEqual(result["call_budget"], result["base_calls"])
        self.assertEqual(result["provider"], "dashscope")

    def test_job_creates_schema_and_review_workspace(self):
        updates = []
        with patch.object(recognition_pipeline, "cache_root", return_value=self.root / "cache"), patch.object(
            recognition_pipeline, "AiClient", FakeAiClient
        ):
            result = recognition_pipeline.RecognitionJob(self.config(), updates.append, threading.Event()).run()
        self.assertEqual(result["status"], "success")
        payload = json.loads((self.output / "recognition_result.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "1.1")
        self.assertEqual(payload["provider"], "dashscope")
        self.assertEqual(payload["model"], "qwen-vl-plus")
        manifest = review_workspace.load_manifest(self.output / "_cache" / "学生甲" / "review_manifest.json")
        self.assertEqual(manifest["import_source"], "recognition_api")
        self.assertEqual(manifest["photo_tasks"][0]["page_review_questions"][0]["decision"], "wrong")

    def test_budget_pause_keeps_recoverable_state(self):
        with patch.object(recognition_pipeline, "cache_root", return_value=self.root / "cache"), patch.object(
            recognition_pipeline, "AiClient", FakeAiClient
        ):
            result = recognition_pipeline.RecognitionJob(self.config(budget=0), lambda _value: None, threading.Event()).run()
        self.assertEqual(result["status"], "budget_paused")
        state = json.loads((self.output / "recognition_job_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "budget_paused")

    def test_legacy_grok_pipeline_module_still_imports(self):
        from src import grok_pipeline
        self.assertIs(grok_pipeline.RecognitionJob, recognition_pipeline.RecognitionJob)



    def test_pdf_index_cache_reused_for_same_pdf(self):
        """Same clean PDF must not re-call the model for page indexing."""
        cache_dir = self.root / "cache"
        calls = {"index": 0}

        class CountingClient(FakeAiClient):
            def index_pdf_pages(self, pages):
                calls["index"] += 1
                return super().index_pdf_pages(pages)

        with patch.object(recognition_pipeline, "cache_root", return_value=cache_dir), patch.object(
            recognition_pipeline, "project_cache_root", return_value=self.root / "project-cache"
        ), patch.object(recognition_pipeline, "AiClient", CountingClient):
            first = recognition_pipeline.RecognitionJob(self.config(), lambda _v: None, threading.Event()).run()
            self.assertEqual(first["status"], "success")
            self.assertEqual(calls["index"], 1)
            self.output = self.root / "输出2"
            self.output.mkdir()
            second = recognition_pipeline.RecognitionJob(self.config(), lambda _v: None, threading.Event()).run()
            self.assertEqual(second["status"], "success")
            self.assertEqual(calls["index"], 1)

        with patch.object(recognition_pipeline, "cache_root", return_value=cache_dir), patch.object(
            recognition_pipeline, "project_cache_root", return_value=self.root / "project-cache"
        ), patch.object(
            recognition_pipeline.ai_settings,
            "summary",
            return_value={"provider": "dashscope", "model": "qwen-vl-plus", "effective_base_url": "https://example.com/v1"},
        ):
            pre = recognition_pipeline.preflight(self.pdf, self.photos.parent, self.root / "结果")
            self.assertTrue(pre["pdf_index_cached"])
            self.assertEqual(pre["pdf_index_calls"], 0)

    def test_pdf_index_cache_is_content_hash_not_provider_bound(self):
        cache_dir = self.root / "cache"
        pages = [{"page_number": 1, "anchor_text": "第9题 力学", "questions": [{"question_no": "9", "bbox": [0.1, 0.1, 0.9, 0.8]}]}]
        pdf_hash = recognition_pipeline._sha256(self.pdf)
        with patch.object(recognition_pipeline, "cache_root", return_value=cache_dir), patch.object(
            recognition_pipeline, "project_cache_root", return_value=self.root / "project-cache"
        ):
            recognition_pipeline.save_pdf_index_cache(
                pdf_hash, pages, provider="zhipu", model="glm-4.6v-flash", page_count=1
            )
            loaded = recognition_pipeline.load_pdf_index_cache(
                pdf_hash, page_count=1, provider="dashscope", model="qwen-vl-plus"
            )
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded["complete"])
            calls, cached, count = recognition_pipeline.estimate_index_calls(
                self.pdf, provider="openai", model="gpt-4o-mini"
            )
            self.assertTrue(cached)
            self.assertEqual(calls, 0)
            self.assertEqual(count, 1)




    def test_exif_orientation_applied_before_recognition(self):
        image = np.zeros((40, 80, 3), dtype=np.uint8)
        image[0:5, 0:5] = (0, 0, 255)
        rotated = recognition_pipeline._apply_exif_orientation(image, 6)
        self.assertEqual(rotated.shape[:2], (80, 40))
        upright = recognition_pipeline._apply_exif_orientation(image, 1)
        self.assertEqual(upright.shape[:2], (40, 80))

if __name__ == "__main__":
    unittest.main()
