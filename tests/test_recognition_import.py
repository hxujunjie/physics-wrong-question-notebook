import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import cv2
import fitz
import numpy as np

from src import recognition_import, review_workspace, teacher_pipeline


class RecognitionImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name) / "飞书测试"
        (self.root / "reference").mkdir(parents=True); (self.root / "students" / "学生甲").mkdir(parents=True); (self.root / "doubao_output").mkdir()
        self.pdf = self.root / "reference" / "原始练习册.pdf"
        document = fitz.open()
        for text in ("第9题", "第10题"):
            page = document.new_page(); page.insert_text((72, 72), text)
        document.save(self.pdf); document.close()
        self.photo = self.root / "students" / "学生甲" / "附件08.jpg"
        self.assertTrue(cv2.imencode(".jpg", np.full((120, 160, 3), 255, dtype=np.uint8))[0])
        cv2.imencode(".jpg", np.full((120, 160, 3), 255, dtype=np.uint8))[1].tofile(str(self.photo))
        self.output = self.root / "结果"

    def tearDown(self): self.temp.cleanup()

    def payload(self, **overrides):
        image = {"student_name":"学生甲", "photo_file":"students/学生甲/附件08.jpg", "matched_reference_file":"reference/原始练习册.pdf", "pdf_page":1, "page_match_confidence":.94, "visible_questions":[{"question_no":"9", "photo_bbox":[.04,.08,.51,.82], "reference_bbox":[.03,.10,.49,.81], "status":"wrong", "number_confidence":.95, "status_confidence":.92, "evidence":"红叉"}, {"question_no":"10", "photo_bbox":[.50,.07,.97,.80], "reference_bbox":[.50,.09,.98,.80], "status":"unknown", "number_confidence":.91, "status_confidence":.89, "evidence":"无法确认"}], "needs_manual_review":False, "review_reason":""}
        image.update(overrides); return {"schema_version":"1.0", "images":[image]}

    def write(self, data):
        path = self.root / "doubao_output" / "recognition_result.json"; path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8"); return path

    def test_normal_import_unknown_stays_pending_and_persists_teacher_changes(self):
        path = self.write(self.payload()); summary = recognition_import.import_recognition_result(path, self.pdf, self.output)
        self.assertEqual(summary["student_count"], 1); self.assertTrue((self.output / "recognition_import" / "raw_recognition_result.json").exists())
        self.assertEqual(summary["mode"], "recognition_api")
        manifest_path = self.output / "_cache" / "学生甲" / "review_manifest.json"; manifest = review_workspace.load_manifest(manifest_path)
        self.assertEqual(manifest["import_source"], "recognition_api")
        first, unknown = manifest["photo_tasks"][0]["page_review_questions"]
        self.assertEqual(first["decision"], "wrong"); self.assertIsNone(unknown["decision"]); self.assertEqual(unknown["suggested_decision"], "unknown")
        changed = review_workspace.update_question(manifest, first["evidence_id"], qnum="9A"); review_workspace.set_decision(manifest, unknown["evidence_id"], "wrong"); review_workspace.save_manifest(manifest_path, manifest)
        loaded = review_workspace.load_manifest(manifest_path); self.assertEqual(loaded["photo_tasks"][0]["page_review_questions"][0]["qnum"], "9A"); self.assertEqual(changed["field_sources"]["qnum"], "teacher")

    def test_missing_reference_bbox_does_not_force_reliable_wrong_into_review(self):
        data = self.payload()
        for question in data["images"][0]["visible_questions"]:
            question["reference_bbox"] = None
        path = self.write(data)
        recognition_import.import_recognition_result(path, self.pdf, self.output)
        manifest = review_workspace.load_manifest(self.output / "_cache" / "学生甲" / "review_manifest.json")
        first, unknown = manifest["photo_tasks"][0]["page_review_questions"]
        self.assertEqual(first["decision"], "wrong")
        self.assertFalse(first["requires_review"])
        self.assertEqual(first["crop_spec"]["source"], "pdf_index")
        self.assertIsNone(unknown["decision"])

    def test_json_syntax_missing_fields_and_validation_errors_are_reported(self):
        path = self.root / "doubao_output" / "recognition_result.json"; path.write_text("{bad", encoding="utf-8")
        result, issues, *_ = recognition_import.validate_recognition(path, self.pdf); self.assertFalse(result["images"]); self.assertIn("JSON", issues[0].reason)
        path = self.write({"schema_version":"1.0", "images":[{}]}); _, issues, *_ = recognition_import.validate_recognition(path, self.pdf); self.assertTrue(any("student_name" in issue.field for issue in issues))
        bad = self.payload(pdf_page=9); bad["images"][0]["visible_questions"][0].update(photo_bbox=[.9,.2,.1,.4], status="bad")
        path = self.write(bad); _, issues, *_ = recognition_import.validate_recognition(path, self.pdf); fields={x.field for x in issues}; self.assertTrue(any("pdf_page" in x for x in fields)); self.assertTrue(any("photo_bbox" in x or "status" in x for x in fields))

    def test_missing_photo_never_becomes_export_photo_crop(self):
        bad = self.payload(photo_file="students/学生甲/不存在.jpg"); path = self.write(bad); result, issues, *_ = recognition_import.validate_recognition(path, self.pdf); self.assertFalse(result["images"]); self.assertTrue(any("photo_file" in x.field for x in issues))
        data = self.payload(pdf_page=None, needs_manual_review=True); path = self.write(data); recognition_import.import_recognition_result(path, self.pdf, self.output)
        manifest = review_workspace.load_manifest(self.output / "_cache" / "学生甲" / "review_manifest.json"); page=manifest["photo_tasks"][0]; question=page["page_review_questions"][0]
        self.assertNotEqual(question["crop_source"], "学生照片")
        self.assertIsNone(recognition_import.crop_imported_question(manifest, page, question))

    def test_same_pdf_hash_allows_clean_pdf_index(self):
        # UI-selected clean PDF is authoritative; import always binds to it (path match).
        copied = self.root / "另一个目录" / "clean.pdf"; copied.parent.mkdir(); copied.write_bytes(self.pdf.read_bytes())
        path = self.write(self.payload())
        result, issues, *_ = recognition_import.validate_recognition(path, copied)
        self.assertFalse([issue for issue in issues if "PDF" in issue.reason])
        self.assertTrue(result["images"][0]["selected_pdf_matches"])
        self.assertEqual(result["images"][0]["pdf_match_reason"], "path")
        self.assertEqual(result["images"][0]["matched_reference_file"], str(copied.resolve()))

    def test_pdf_crop_and_next_page_can_save_unresolved(self):
        path=self.write(self.payload()); recognition_import.import_recognition_result(path, self.pdf, self.output)
        manifest_path=self.output / "_cache" / "学生甲" / "review_manifest.json"; manifest=review_workspace.load_manifest(manifest_path); page=manifest["photo_tasks"][0]
        self.assertIsNotNone(recognition_import.crop_imported_question(manifest, page, page["page_review_questions"][0]))
        self.assertFalse(review_workspace.page_can_complete(page)[0]); review_workspace.mark_page_complete(page, allow_unresolved=True); review_workspace.save_manifest(manifest_path, manifest)
        rebuilt=teacher_pipeline.rebuild_student_pdf(self.output, "学生甲"); self.assertEqual(rebuilt["wrong_question_count"], 1)

    def test_legacy_photo_crop_is_upgraded_to_clean_pdf(self):
        path = self.write(self.payload()); recognition_import.import_recognition_result(path, self.pdf, self.output)
        manifest_path = self.output / "_cache" / "学生甲" / "review_manifest.json"; manifest = review_workspace.load_manifest(manifest_path)
        page = manifest["photo_tasks"][0]; question = page["page_review_questions"][0]
        question["crop_spec"]["source"] = "photo"; question["crop_source"] = "学生照片"; question["decision"] = "wrong"
        page["review_completed"] = True; review_workspace.save_manifest(manifest_path, manifest)
        rebuilt = teacher_pipeline.rebuild_student_pdf(self.output, "学生甲")
        self.assertEqual(rebuilt["wrong_question_count"], 1)
        loaded = review_workspace.load_manifest(manifest_path)
        self.assertIn(
            loaded["photo_tasks"][0]["page_review_questions"][0]["crop_resolution"]["source"],
            {"原始PDF（reference_bbox）", "原始PDF（reference_bbox 兜底）", "原始PDF（页码+题号索引）"},
        )

    def test_page_completion_defers_pdf_rebuild(self):
        path = self.write(self.payload())
        recognition_import.import_recognition_result(path, self.pdf, self.output)
        manifest_path = self.output / "_cache" / "学生甲" / "review_manifest.json"
        manifest = review_workspace.load_manifest(manifest_path)
        page = manifest["photo_tasks"][0]
        with patch.object(teacher_pipeline, "rebuild_student_pdf") as rebuild:
            teacher_pipeline.complete_review_page(self.output, "学生甲", page["photo_sha256"], allow_unresolved=True)
        rebuild.assert_not_called()
        self.assertTrue(review_workspace.load_manifest(manifest_path)["pdf_dirty"])

    def test_legacy_manifest_remains_loadable(self):
        legacy={"schema_version":1,"student":"旧学生","photo_tasks":[],"legacy_baseline":{},"pdf_dirty":False}
        target=self.output / "_cache" / "旧学生" / "review_manifest.json"; review_workspace.save_manifest(target, legacy)
        self.assertEqual(review_workspace.load_manifest(target)["student"], "旧学生")


    def test_legacy_doubao_import_module_still_imports(self):
        from src import doubao_import
        self.assertIs(doubao_import.import_recognition_result, recognition_import.import_recognition_result)

    def test_filename_only_photo_resolves_via_photo_root(self):
        """Teachers/Grok can write basename only; UI supplies photo_root + clean PDF."""
        data = self.payload()
        data["images"][0]["photo_file"] = "附件08.jpg"
        data["images"][0].pop("matched_reference_file", None)
        data["images"][0]["pdf_page"] = "1"  # string page from external models
        path = self.write(data)
        photo_root = self.root / "students"
        summary = recognition_import.import_recognition_result(
            path, self.pdf, self.output / "by_name", photo_root=photo_root
        )
        self.assertEqual(summary["student_count"], 1)
        manifest = review_workspace.load_manifest(
            self.output / "by_name" / "_cache" / "学生甲" / "review_manifest.json"
        )
        photo_path = Path(manifest["photo_tasks"][0]["photo_path"])
        self.assertTrue(photo_path.is_file())
        self.assertEqual(photo_path.name, "附件08.jpg")
        self.assertEqual(manifest["clean_pdf_path"], str(self.pdf.resolve()))
        self.assertEqual(manifest["photo_tasks"][0]["matched_pdf_page"], 1)



    def test_finalize_waits_when_pages_incomplete_without_clearing_dirty(self):
        path = self.write(self.payload())
        recognition_import.import_recognition_result(path, self.pdf, self.output)
        first = teacher_pipeline.finalize_delivery(self.output, allow_incomplete=False)
        self.assertEqual(first["status"], "review_required")
        self.assertEqual(first.get("rebuilt"), [])
        manifest = review_workspace.load_manifest(self.output / "_cache" / "学生甲" / "review_manifest.json")
        self.assertTrue(manifest.get("pdf_dirty"))
        page = manifest["photo_tasks"][0]
        teacher_pipeline.complete_review_page(self.output, "学生甲", page["photo_sha256"], allow_unresolved=True)
        second = teacher_pipeline.finalize_delivery(self.output, allow_incomplete=False)
        self.assertEqual(second["status"], "success")
        self.assertTrue(second.get("rebuilt"))
        self.assertTrue(any(item.get("pdf") for item in second["rebuilt"]))




    def test_export_prefers_page_qnum_over_reference_bbox(self):
        """AI reference_bbox may drift to the previous question; export must prefer page+qnum."""
        path = self.write(self.payload())
        recognition_import.import_recognition_result(path, self.pdf, self.output)
        manifest_path = self.output / "_cache" / "学生甲" / "review_manifest.json"
        manifest = review_workspace.load_manifest(manifest_path)
        question = manifest["photo_tasks"][0]["page_review_questions"][0]
        question["decision"] = "wrong"
        question["qnum"] = "9"
        # Deliberately wrong bbox that would crop something else if trusted first.
        question["crop_spec"] = {
            "source": "pdf_bbox",
            "reference_bbox": [0.01, 0.01, 0.2, 0.2],
            "pdf_page_index_0based": 0,
            "question_no": "9",
        }
        review_workspace.save_manifest(manifest_path, manifest)
        with patch.object(teacher_pipeline, "_manual_segments", return_value=(None, None)), patch.object(
            recognition_import, "_resolve_pdf_index"
        ) as resolve:
            resolve.return_value = (
                (
                    [type("S", (), {"page_index": 0, "is_continuation": False})()],
                    [__import__("numpy").zeros((20, 30, 3), dtype="uint8")],
                ),
                None,
            )
            segs, error, source = teacher_pipeline._strict_pdf_segments(manifest, question, None)
        self.assertIsNone(error)
        self.assertIn("页码+题号", source)
        resolve.assert_called()

if __name__ == "__main__":
    unittest.main()
