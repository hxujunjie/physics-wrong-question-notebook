import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src import review_workspace


def question(qnum, column, y):
    return SimpleNamespace(qnum=str(qnum), column=column, top_y=y, bottom_y=y+120,
        bbox=(100+column*500,y,130+column*500,y+30), column_x0=80+column*500,
        column_x1=480+column*500, question_id=f"q{qnum}", page_index=65)


class FakeIndex:
    def __init__(self, questions): self.questions=questions
    def page_questions(self, _page): return self.questions
    def question_segments(self, q):
        return [SimpleNamespace(page_index=65,bbox=(q.column_x0,q.top_y,q.column_x1,q.bottom_y),is_continuation=False)]


class ReviewWorkspaceTests(unittest.TestCase):
    def test_all_questions_and_reading_order(self):
        index=FakeIndex([question(4,1,100),question(2,0,300),question(1,0,100),question(5,1,300)])
        result={"registration_reliable":False,"alignment_confidence":0,"marks":[],"candidate_questions":[],"review_questions":[]}
        items=review_workspace.build_page_review_questions(index,65,result,None,(1000,1000,3))
        self.assertEqual([q["qnum"] for q in items],["1","2","4","5"])
        self.assertEqual([q["reading_order"] for q in items],[0,1,2,3])

    def test_reverse_homography_mapping_and_unreliable_null(self):
        q=question(1,0,100); H=np.array([[2,0,0],[0,2,0],[0,0,1]],float)
        anchor,polygon=review_workspace.reverse_map_question(q,H,(500,500,3),.9,True)
        self.assertIsNotNone(anchor); self.assertIsNotNone(polygon)
        self.assertAlmostEqual(anchor[0],.115,places=2)
        self.assertEqual(review_workspace.reverse_map_question(q,H,(500,500,3),.1,True),(None,None))

    def test_low_confidence_null_high_confidence_prefill(self):
        index=FakeIndex([question(1,0,100),question(2,0,300),question(3,1,100)])
        result={"registration_reliable":False,"alignment_confidence":0,
          "marks":[{"type":"cross","confidence":.9},{"type":"unknown","confidence":.2}],
          "candidate_questions":[{"source_question_id":"q1","source_mark_idx":0}],
          "review_questions":[{"source_question_id":"q2","source_mark_idx":1,"reason":"low"}]}
        items=review_workspace.build_page_review_questions(index,65,result,None,(100,100,3))
        self.assertEqual(items[0]["decision"],"wrong")
        self.assertIsNone(items[1]["decision"])
        self.assertEqual(items[2]["decision"],"correct")

    def test_persistence_evidence_independence_and_legacy(self):
        e1=review_workspace.evidence_id("甲","a","q4"); e2=review_workspace.evidence_id("甲","b","q4")
        self.assertNotEqual(e1,e2)
        manifest={"legacy_baseline":{"q4":2},"pdf_dirty":False,"photo_tasks":[
          {"page_review_questions":[{"source_question_id":"q4","evidence_id":e1,"decision":"wrong","content_complete":True,"suggested_decision":"wrong"}]},
          {"page_review_questions":[{"source_question_id":"q4","evidence_id":e2,"decision":"wrong","content_complete":True,"suggested_decision":"wrong"}]}]}
        self.assertEqual(review_workspace.evidence_counts(manifest)["q4"],4)
        review_workspace.set_decision(manifest,e1,"correct")
        self.assertEqual(review_workspace.evidence_counts(manifest)["q4"],3)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"review_manifest.json"; review_workspace.save_manifest(path,manifest)
            loaded=review_workspace.load_manifest(path)
            self.assertEqual(loaded["photo_tasks"][0]["page_review_questions"][0]["decision"],"correct")
            self.assertTrue(loaded["pdf_dirty"])

    def test_incomplete_wrong_excluded_and_page_gate(self):
        page={"page_review_questions":[{"requires_review":True,"decision":None}]}
        self.assertEqual(review_workspace.page_can_complete(page),(False,1))
        with self.assertRaises(ValueError): review_workspace.mark_page_complete(page)
        page["page_review_questions"][0]["decision"]="wrong"
        review_workspace.mark_page_complete(page); self.assertTrue(page["review_completed"])
        manifest={"legacy_baseline":{},"photo_tasks":[{"page_review_questions":[{"source_question_id":"q","decision":"wrong","content_complete":False}]}]}
        self.assertEqual(review_workspace.evidence_counts(manifest),{})

    def test_legacy_pending_migration(self):
        with tempfile.TemporaryDirectory() as d:
            pending=Path(d)/"待确认.json"; pending.write_text(json.dumps([{"qnum":"7","reason":"旧记录"}],ensure_ascii=False),encoding="utf-8")
            manifest={"photo_tasks":[]}; self.assertEqual(review_workspace.migrate_legacy_pending(manifest,pending),1)
            self.assertTrue(manifest["photo_tasks"][0]["historical_without_full_page"])


    def test_photo_display_rotation_persists_and_maps_points(self):
        manifest={"photo_tasks":[{"photo_sha256":"abc","page_review_questions":[]}]}
        page=review_workspace.set_photo_display_rotation(manifest, "abc", 90)
        self.assertEqual(page["display_rotation_deg"], 90)
        self.assertEqual(review_workspace._rotate_norm_point(0.2, 0.1, 90), [0.9, 0.2])
        self.assertEqual(review_workspace._rotate_norm_point(0.2, 0.1, 180), [0.8, 0.9])
        self.assertEqual(review_workspace._rotate_norm_point(0.2, 0.1, 270), [0.1, 0.8])
        with self.assertRaises(ValueError):
            review_workspace.set_photo_display_rotation(manifest, "abc", 45)



    def test_manual_crop_syncs_page_to_siblings(self):
        manifest = {
            "photo_tasks": [{
                "photo_sha256": "p1",
                "matched_pdf_page": 11,
                "matched_pdf_page_index_0based": 10,
                "registration_reliable": True,
                "page_review_questions": [
                    {
                        "evidence_id": "e1",
                        "qnum": "5",
                        "page_index": 10,
                        "crop_spec": {"source": "pdf_index", "pdf_page_index_0based": 10, "question_no": "5"},
                        "crop_source": "原始PDF",
                    },
                    {
                        "evidence_id": "e2",
                        "qnum": "6",
                        "page_index": 10,
                        "crop_spec": {"source": "pdf_index", "pdf_page_index_0based": 10, "question_no": "6"},
                        "crop_source": "原始PDF",
                    },
                    {
                        "evidence_id": "e3",
                        "qnum": "7",
                        "page_index": 10,
                        "crop_spec": {
                            "source": "pdf_manual",
                            "pdf_page_index_0based": 10,
                            "question_no": "7",
                            "manual_segments": [{"page_index": 10, "bbox_norm": [0.1, 0.1, 0.4, 0.4]}],
                        },
                        "crop_source": "原始PDF",
                        "crop_method": "教师手动框选",
                    },
                ],
            }]
        }
        result = review_workspace.apply_manual_crop(
            manifest,
            "e1",
            [{"page_index": 20, "bbox_norm": [0.2, 0.2, 0.8, 0.8]}],
            sync_page_siblings=True,
        )
        self.assertTrue(result["page_changed"])
        # synced_count excludes the question currently being cropped.
        self.assertEqual(result["synced_count"], 1)
        page = result["page"]
        self.assertEqual(page["matched_pdf_page"], 21)
        self.assertEqual(page["matched_pdf_page_index_0based"], 20)
        q1, q2, q3 = page["page_review_questions"]
        self.assertEqual(q1["page_index"], 20)
        self.assertEqual(q1["crop_spec"]["source"], "pdf_manual")
        self.assertEqual(q2["page_index"], 20)
        self.assertEqual(q2["crop_spec"]["source"], "pdf_index")
        self.assertEqual(q2["crop_hint"].find("第 21 页") >= 0 or "21" in q2["crop_hint"], True)
        # previously manual sibling keeps geometry, page metadata updates
        self.assertEqual(q3["page_index"], 20)
        self.assertEqual(q3["crop_spec"]["source"], "pdf_manual")
        self.assertEqual(len(q3["crop_spec"]["manual_segments"]), 1)



    def test_sync_photo_task_pdf_page_from_header(self):
        manifest = {
            "photo_tasks": [{
                "photo_sha256": "p1",
                "matched_pdf_page": 5,
                "matched_pdf_page_index_0based": 4,
                "page_review_questions": [
                    {"evidence_id": "a", "qnum": "1", "page_index": 4, "crop_spec": {"source": "pdf_index", "pdf_page_index_0based": 4, "question_no": "1"}},
                    {"evidence_id": "b", "qnum": "2", "page_index": 4, "crop_spec": {"source": "pdf_index", "pdf_page_index_0based": 4, "question_no": "2"}},
                ],
            }]
        }
        result = review_workspace.sync_photo_task_pdf_page(manifest, "p1", 28, actor="teacher_header_page")
        self.assertTrue(result["page_changed"])
        self.assertEqual(result["pdf_page"], 29)
        self.assertEqual(result["synced_count"], 2)
        self.assertEqual(result["page"]["matched_pdf_page"], 29)
        self.assertEqual(result["page"]["page_review_questions"][1]["page_index"], 28)
        self.assertEqual(result["page"]["page_review_questions"][1]["crop_spec"]["source"], "pdf_index")

if __name__ == "__main__": unittest.main()
