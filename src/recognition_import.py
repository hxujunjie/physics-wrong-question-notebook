"""Import validated AI recognition JSON into the teacher review workspace.

This module deliberately does not call a model.  It is the boundary between a
recognition result (online pipeline or external JSON) and the review manifest.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np

from . import recognition_pipeline, review_workspace


SUPPORTED_SCHEMA_VERSIONS = {"1.0", "1.1"}
LOW_CONFIDENCE = 0.80
# New writes use recognition_api; legacy grok_api / doubao_json remain readable.
IMPORT_SOURCE_API = "recognition_api"
LEGACY_IMPORT_SOURCES = frozenset({"recognition_api", "grok_api", "doubao_json", "recognition_json"})


@dataclass
class ImportIssue:
    file: str
    field: str
    reason: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {"file": self.file, "field": self.field, "reason": self.reason, "severity": self.severity}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_input(path: str | Path) -> tuple[Path, Path]:
    """Return JSON file and project root for either supported chooser value."""
    selected = Path(path).expanduser().resolve()
    if selected.is_file():
        json_path = selected
        root = selected.parent.parent if selected.parent.name == "doubao_output" else selected.parent
    elif selected.is_dir():
        candidate = selected / "doubao_output" / "recognition_result.json"
        json_path = candidate if candidate.is_file() else selected / "recognition_result.json"
        if not json_path.is_file():
            raise ValueError("未找到 recognition_result.json")
        root = selected if (selected / "students").is_dir() or (selected / "reference").is_dir() else selected.parent
    else:
        raise ValueError("recognition_result.json 不存在")
    return json_path, root


def _relative_path(root: Path, value: Any, field: str, issues: list[ImportIssue], source: str) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        issues.append(ImportIssue(source, field, "缺少路径")); return None
    raw = Path(value.replace("/", "\\"))
    path = raw if raw.is_absolute() else root / raw
    try:
        return path.resolve()
    except OSError:
        issues.append(ImportIssue(source, field, "路径无法解析")); return None


def _bbox(value: Any, field: str, issues: list[ImportIssue], source: str) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        issues.append(ImportIssue(source, field, "必须是包含 4 个数字的 bbox")); return None
    try:
        x0, y0, x1, y1 = [float(number) for number in value]
    except (TypeError, ValueError):
        issues.append(ImportIssue(source, field, "bbox 必须全部为数值")); return None
    if any(number < 0 or number > 1 for number in (x0, y0, x1, y1)):
        issues.append(ImportIssue(source, field, "bbox 数值必须在 0 到 1 之间")); return None
    if x0 >= x1 or y0 >= y1:
        issues.append(ImportIssue(source, field, "bbox 必须满足 left < right 且 top < bottom")); return None
    return [x0, y0, x1, y1]


def _confidence(value: Any, field: str, issues: list[ImportIssue], source: str) -> float | None:
    try: number = float(value)
    except (TypeError, ValueError):
        issues.append(ImportIssue(source, field, "置信度必须是 0 到 1 的数值")); return None
    if number < 0 or number > 1:
        issues.append(ImportIssue(source, field, "置信度必须在 0 到 1 之间")); return None
    return number


def validate_recognition(path: str | Path, selected_pdf: str | Path | None = None) -> tuple[dict, list[ImportIssue], Path, Path]:
    json_path, root = resolve_input(path)
    issues: list[ImportIssue] = []
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"schema_version": None, "images": []}, [ImportIssue(str(json_path), "$", f"JSON 无法解析: {exc}")], json_path, root
    if not isinstance(payload, dict):
        return {"schema_version": None, "images": []}, [ImportIssue(str(json_path), "$", "JSON 根节点必须是对象")], json_path, root
    version = payload.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        issues.append(ImportIssue(str(json_path), "schema_version", f"不支持的版本: {version!r}，仅支持 {sorted(SUPPORTED_SCHEMA_VERSIONS)}"))
    images = payload.get("images")
    if not isinstance(images, list):
        issues.append(ImportIssue(str(json_path), "images", "必须是数组")); images = []
    selected = Path(selected_pdf).expanduser().resolve() if selected_pdf else None
    if selected is not None and (not selected.is_file() or selected.suffix.lower() != ".pdf"):
        issues.append(ImportIssue(str(selected), "selected_pdf", "所选原始练习册 PDF 不存在或不可读取"))
        selected = None
    selected_hash = _sha256(selected) if selected is not None else None
    reference_hashes: dict[Path, str | None] = {}
    validated: list[dict] = []
    seen: set[tuple[str, str]] = set()
    page_counts: dict[Path, int] = {}
    for image_index, image in enumerate(images):
        prefix = f"images[{image_index}]"
        if not isinstance(image, dict):
            issues.append(ImportIssue(str(json_path), prefix, "记录必须是对象")); continue
        student = image.get("student_name")
        if not isinstance(student, str) or not student.strip():
            issues.append(ImportIssue(str(json_path), prefix + ".student_name", "必须是非空字符串")); continue
        photo = _relative_path(root, image.get("photo_file"), prefix + ".photo_file", issues, str(json_path))
        reference = _relative_path(root, image.get("matched_reference_file"), prefix + ".matched_reference_file", issues, str(json_path))
        if photo is None or not photo.is_file():
            issues.append(ImportIssue(str(json_path), prefix + ".photo_file", "照片文件不存在或不可读")); continue
        if reference is not None and (not reference.is_file() or reference.suffix.lower() != ".pdf"):
            issues.append(ImportIssue(str(json_path), prefix + ".matched_reference_file", "PDF 不存在、不可读或不是 PDF")); reference = None
        page = image.get("pdf_page")
        page_index = None
        if page is not None:
            if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                issues.append(ImportIssue(str(json_path), prefix + ".pdf_page", "PDF 页码必须是从 1 开始的正整数"))
            elif reference:
                try:
                    if reference not in page_counts:
                        with fitz.open(reference) as document:
                            page_counts[reference] = document.page_count
                    count = page_counts[reference]
                    if page > count: issues.append(ImportIssue(str(json_path), prefix + ".pdf_page", f"页码越界：PDF 只有 {count} 页"))
                    else: page_index = page - 1
                except Exception as exc: issues.append(ImportIssue(str(json_path), prefix + ".matched_reference_file", f"无法读取 PDF: {exc}"))
        elif image.get("needs_manual_review") is not True:
            issues.append(ImportIssue(str(json_path), prefix + ".pdf_page", "缺少页码；将仅允许学生照片裁剪", "warning"))
        match_conf = _confidence(image.get("page_match_confidence", 0), prefix + ".page_match_confidence", issues, str(json_path))
        questions: list[dict] = []
        if not isinstance(image.get("visible_questions"), list):
            issues.append(ImportIssue(str(json_path), prefix + ".visible_questions", "必须是数组")); continue
        for q_index, question in enumerate(image["visible_questions"]):
            qprefix = f"{prefix}.visible_questions[{q_index}]"
            if not isinstance(question, dict): issues.append(ImportIssue(str(json_path), qprefix, "题目记录必须是对象")); continue
            number = question.get("question_no")
            if not isinstance(number, (str, int)) or not str(number).strip(): issues.append(ImportIssue(str(json_path), qprefix + ".question_no", "题号必须是非空字符串或整数")); continue
            number = str(number).strip(); key = (str(photo), number)
            if key in seen: issues.append(ImportIssue(str(json_path), qprefix + ".question_no", "同一图片的题号重复")); continue
            seen.add(key)
            photo_bbox = _bbox(question.get("photo_bbox"), qprefix + ".photo_bbox", issues, str(json_path))
            ref_bbox = _bbox(question.get("reference_bbox"), qprefix + ".reference_bbox", issues, str(json_path)) if question.get("reference_bbox") is not None else None
            status = question.get("status")
            if status not in {"wrong", "correct", "unknown"}: issues.append(ImportIssue(str(json_path), qprefix + ".status", "status 只能是 wrong、correct 或 unknown")); continue
            number_conf = _confidence(question.get("number_confidence"), qprefix + ".number_confidence", issues, str(json_path))
            status_conf = _confidence(question.get("status_confidence"), qprefix + ".status_confidence", issues, str(json_path))
            if photo_bbox is None or number_conf is None or status_conf is None: continue
            questions.append({"question_no": number, "photo_bbox": photo_bbox, "reference_bbox": ref_bbox, "status": status, "number_confidence": number_conf, "status_confidence": status_conf, "evidence": str(question.get("evidence") or "")})
        if questions:
            reference_matches = selected is None
            match_reason = "mismatch"
            if selected is not None and reference is not None:
                if selected == reference:
                    reference_matches, match_reason = True, "path"
                else:
                    reference_hashes.setdefault(reference, _sha256(reference) if reference.is_file() else None)
                    if selected_hash and reference_hashes[reference] == selected_hash:
                        reference_matches, match_reason = True, "sha256"
            validated.append({"student_name": student.strip(), "photo_file": str(photo), "matched_reference_file": str(reference) if reference else None, "pdf_page": page_index + 1 if page_index is not None else None, "pdf_page_index_0based": page_index, "page_match_confidence": match_conf or 0.0, "needs_manual_review": bool(image.get("needs_manual_review")), "review_reason": str(image.get("review_reason") or ""), "visible_questions": questions, "selected_pdf_matches": bool(reference_matches), "pdf_match_reason": match_reason})
    return {"schema_version": version, "images": validated}, issues, json_path, root


def _question(photo_hash: str, record: dict, image: dict, order: int) -> dict:
    bbox = record["photo_bbox"]; x0, y0, x1, y1 = bbox
    status = record["status"]; confidence = min(record["number_confidence"], record["status_confidence"])
    page_ok = image["pdf_page_index_0based"] is not None and image["selected_pdf_matches"] and record.get("reference_bbox") is not None
    reliable = page_ok and image["page_match_confidence"] >= LOW_CONFIDENCE and not image["needs_manual_review"]
    needs_review = status == "unknown" or confidence < LOW_CONFIDENCE or not reliable
    decision = None if needs_review else status
    source_id = f"ai:{photo_hash}:{record['question_no']}"
    return {"source_question_id": source_id, "evidence_id": review_workspace.evidence_id(image["student_name"], photo_hash, source_id), "qnum": record["question_no"], "page_index": image["pdf_page_index_0based"], "reading_order": order, "segments": [], "suggested_decision": status, "decision": decision, "confidence": confidence, "requires_review": needs_review, "reason": image["review_reason"] or ("页面/PDF 匹配需要人工复核" if not reliable else ("unknown 结果必须人工确认" if status == "unknown" else "低置信度 AI 结果")), "evidence": record["evidence"], "mark_indices": [], "mark_types": [status], "photo_anchor_norm": [(x0+x1)/2, (y0+y1)/2], "photo_polygon_norm": [[x0,y0],[x1,y0],[x1,y1],[x0,y1]], "content_complete": True, "content_error": None, "teacher_modified": False, "viewed": False, "crop_source": "原始PDF" if reliable else "学生照片", "crop_spec": {"source": "pdf" if reliable else "photo", "reference_bbox": record.get("reference_bbox"), "photo_bbox": bbox, "pdf_page_index_0based": image["pdf_page_index_0based"]}, "ai_result": record, "field_sources": {"qnum":"ai", "decision":"ai", "crop_source":"ai"}, "modification_history": []}


def _question_v2(photo_hash: str, record: dict, image: dict, order: int) -> dict:
    """Build a review item without confusing crop metadata with AI certainty."""
    bbox = record["photo_bbox"]
    x0, y0, x1, y1 = bbox
    status = record["status"]
    confidence = min(record["number_confidence"], record["status_confidence"])
    page_reliable = (
        image["pdf_page_index_0based"] is not None
        and image["selected_pdf_matches"]
        and image["page_match_confidence"] >= LOW_CONFIDENCE
    )
    needs_review = status == "unknown" or confidence < LOW_CONFIDENCE or not page_reliable
    decision = None if needs_review else status
    source_id = f"ai:{photo_hash}:{record['question_no']}"
    # Prefer page+question-number for export. Keep AI reference_bbox only as fallback data —
    # free VL models often attach the right number to a slightly shifted box.
    if page_reliable and str(record.get("question_no") or "").strip():
        crop_spec_source, crop_method = "pdf_index", "页码+题号索引"
        crop_source = "原始PDF"
        crop_ready = True
        crop_action = "auto"
        crop_hint = "可自动裁切（页码+题号）"
    elif page_reliable:
        crop_spec_source, crop_method = "pdf_pending", "缺少题号"
        crop_source = "待定位"
        crop_ready = False
        crop_action = "fix_qnum"
        crop_hint = "请先填写题号；若仍失败再手动框选"
    else:
        # A photo bbox is only a review overlay.  It must never become an
        # export crop source because final wrong books are clean-PDF only.
        crop_spec_source, crop_method = "pdf_pending", "等待教师确认 PDF 页码/题号"
        crop_source = "待定位"
        crop_ready = False
        crop_action = "manual_or_page"
        crop_hint = "页码不可靠：请确认 PDF 页或手动框选"
    reason = (
        "页面/PDF 匹配需要人工复核" if not page_reliable else
        "unknown 结果必须人工确认" if status == "unknown" else
        "低置信度 AI 结果必须人工确认" if confidence < LOW_CONFIDENCE else ""
    )
    return {
        "source_question_id": source_id,
        "evidence_id": review_workspace.evidence_id(image["student_name"], photo_hash, source_id),
        "qnum": record["question_no"], "page_index": image["pdf_page_index_0based"],
        "reading_order": order, "segments": [], "suggested_decision": status,
        "decision": decision, "confidence": confidence, "requires_review": needs_review,
        "reason": reason, "evidence": record["evidence"], "mark_indices": [],
        "mark_types": [status], "photo_anchor_norm": [(x0+x1)/2, (y0+y1)/2],
        "photo_polygon_norm": [[x0,y0],[x1,y0],[x1,y1],[x0,y1]],
        "content_complete": crop_ready, "content_error": None if crop_ready else crop_hint,
        "teacher_modified": False,
        "viewed": False, "crop_source": crop_source,
        "crop_method": crop_method,
        "crop_action": crop_action,
        "crop_hint": crop_hint,
        "crop_spec": {
            "source": crop_spec_source,
            # retained only as export fallback after page+qnum fails
            "reference_bbox": record.get("reference_bbox"),
            "photo_bbox": bbox,
            "pdf_page_index_0based": image["pdf_page_index_0based"],
            "question_no": record["question_no"],
        },
        "ai_result": record, "field_sources": {"qnum":"ai", "decision":"ai", "crop_source":"ai"},
        "modification_history": [],
    }


def import_recognition_result(input_path: str | Path, selected_pdf: str | Path, output_dir: str | Path) -> dict:
    """Validate, persist and import all valid records; invalid rows stay reportable."""
    result, issues, json_path, root = validate_recognition(input_path, selected_pdf)
    output = Path(output_dir).expanduser().resolve(); output.mkdir(parents=True, exist_ok=True)
    archive = output / "recognition_import"; archive.mkdir(exist_ok=True)
    shutil.copy2(json_path, archive / "raw_recognition_result.json")
    (archive / "validated_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (archive / "import_issues.json").write_text(json.dumps([i.as_dict() for i in issues], ensure_ascii=False, indent=2), encoding="utf-8")
    selected = Path(selected_pdf).expanduser().resolve()
    grouped: dict[str, list[dict]] = {}
    for image in result["images"]: grouped.setdefault(image["student_name"], []).append(image)
    summaries = []
    for student, images in grouped.items():
        pages = []
        for image in images:
            photo = Path(image["photo_file"]); photo_hash = _sha256(photo)
            questions = [_question_v2(photo_hash, record, image, index) for index, record in enumerate(image["visible_questions"])]
            # Prefer an upright derivative for review so landscape phone photos appear
            # correct without a manual rotate step. Fall back to the original path.
            try:
                upright = recognition_pipeline.prepare_review_photo(photo, output, student, photo_hash)
                rectified = upright["rectified_photo"]
                display_rotation = upright.get("display_rotation_deg", 0)
                orientation_meta = upright.get("orientation_meta")
            except Exception:
                rectified = str(photo)
                display_rotation = 0
                orientation_meta = None
            pages.append({
                "photo_path": str(photo),
                "photo_name": photo.name,
                "photo_sha256": photo_hash,
                "rectified_photo": rectified,
                "legacy_preview": None,
                "display_rotation_deg": display_rotation,
                "orientation_meta": orientation_meta,
                "matched_pdf_page": image["pdf_page"],
                "matched_pdf_page_index_0based": image["pdf_page_index_0based"],
                "registration_confidence": image["page_match_confidence"],
                "registration_reliable": bool(image["pdf_page_index_0based"] is not None and image["selected_pdf_matches"] and image["page_match_confidence"] >= LOW_CONFIDENCE and not image["needs_manual_review"]),
                "match_review_reason": image["review_reason"] or ("PDF 页不可用或与所选原始练习册不一致" if not image["selected_pdf_matches"] else ""),
                "review_completed": False,
                "import_source": IMPORT_SOURCE_API,
                "page_review_questions": questions,
            })
        for page_record, image_record in zip(pages, images):
            page_record["pdf_match_reason"] = image_record.get("pdf_match_reason", "mismatch")
        manifest = {"schema_version": review_workspace.SCHEMA_VERSION, "student": student, "clean_pdf_path": str(selected), "clean_pdf_sha256": _sha256(selected), "ocr_pdf_path": None, "import_source": IMPORT_SOURCE_API, "recognition_result_path": str(json_path), "recognition_project_root": str(root), "photo_tasks": pages, "legacy_baseline": {}, "created_at": review_workspace.now_iso(), "last_modified_at": review_workspace.now_iso(), "pdf_dirty": True}
        target = output / "_cache" / student / "review_manifest.json"; review_workspace.save_manifest(target, manifest)
        summaries.append({"student": student, "photo_count": len(pages), "wrong_question_count": sum(1 for p in pages for q in p["page_review_questions"] if q["decision"] == "wrong"), "review_count": sum(1 for p in pages for q in p["page_review_questions"] if q["requires_review"]), "low_confidence_count": sum(1 for p in pages for q in p["page_review_questions"] if q["requires_review"]), "reviewed_page_count": 0, "total_review_page_count": len(pages), "failed_photo_count": 0, "review_manifest": str(target), "pdf": None})
    summary = {"status": "partial" if issues else "success", "mode": IMPORT_SOURCE_API, "pdf_path": str(selected), "output_dir": str(output), "student_count": len(summaries), "photo_count": sum(x["photo_count"] for x in summaries), "wrong_question_count": sum(x["wrong_question_count"] for x in summaries), "review_count": sum(x["review_count"] for x in summaries), "failed_student_count": 0, "issues": [i.as_dict() for i in issues], "students": summaries}
    (output / "班级汇总.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _resolve_pdf_index(manifest: dict, spec: dict, source_index=None):
    page_index = spec.get("pdf_page_index_0based")
    qnum = str(spec.get("question_no") or "").strip()
    if not isinstance(page_index, int) or page_index < 0 or not qnum:
        return None, "缺少有效的 PDF 页码或题号"
    if source_index is None:
        from . import config, ocr_questions
        source_index = ocr_questions.SourceBookIndex(
            manifest["clean_pdf_path"], dpi=config.PDF_RENDER_DPI,
            index_pdf_path=manifest.get("ocr_pdf_path") or None,
        )
    source_index.ensure_pages([page_index])
    matches = [q for q in source_index.page_questions(page_index) if str(q.qnum).strip() == qnum]
    if len(matches) != 1:
        return None, f"指定 PDF 第 {page_index + 1} 页找到题号 {qnum} 的数量为 {len(matches)}"
    segments = source_index.question_segments(matches[0])
    if not segments:
        return None, f"PDF 第 {page_index + 1} 页题号 {qnum} 没有可裁剪片段"
    source_index.ensure_pages([s.page_index for s in segments])
    images = source_index.extract_segments(segments)
    if len(images) != len(segments) or any(image is None or image.size == 0 for image in images):
        return None, f"PDF 第 {page_index + 1} 页题号 {qnum} 的题目片段读取失败"
    return (segments, images), None


def crop_imported_question(manifest: dict, page: dict, question: dict, source_index=None) -> np.ndarray | None:
    """Crop from the clean PDF only.

    Preference matches export:
    1. page + question number index
    2. AI/reference bbox on the clean PDF
    Never return a student-photo crop for final content.
    """
    spec = question.get("crop_spec") or {}
    page_index = spec.get("pdf_page_index_0based")
    qnum = str(spec.get("question_no") or question.get("qnum") or "").strip()
    errors: list[str] = []

    # 1) Prefer page + qnum whenever possible, even if source still says pdf_bbox.
    if isinstance(page_index, int) and qnum:
        resolved, error = _resolve_pdf_index(
            manifest,
            {"pdf_page_index_0based": page_index, "question_no": qnum},
            source_index,
        )
        if resolved is not None:
            question["crop_resolution"] = {"source": "原始PDF", "method": "页码+题号索引", "error": None}
            return np.vstack(resolved[1])
        if error:
            errors.append(error)

    # 2) Fall back to reference_bbox on the clean PDF.
    ref_bbox = spec.get("reference_bbox")
    if isinstance(page_index, int) and isinstance(ref_bbox, list) and len(ref_bbox) == 4:
        x0, y0, x1, y1 = [float(v) for v in ref_bbox]
        try:
            document = fitz.open(manifest["clean_pdf_path"])
            page_obj = document.load_page(page_index)
            rect = page_obj.rect
            pix = page_obj.get_pixmap(
                matrix=fitz.Matrix(2, 2),
                clip=fitz.Rect(
                    rect.x0 + x0 * rect.width,
                    rect.y0 + y0 * rect.height,
                    rect.x0 + x1 * rect.width,
                    rect.y0 + y1 * rect.height,
                ),
                alpha=False,
            )
            image = cv2.imdecode(np.frombuffer(pix.tobytes("png"), dtype=np.uint8), cv2.IMREAD_COLOR)
            document.close()
            if image is not None and image.size:
                question["crop_resolution"] = {
                    "source": "原始PDF",
                    "method": "reference_bbox 兜底",
                    "error": "; ".join(errors) if errors else None,
                }
                return image
        except Exception as exc:
            errors.append(str(exc))

    question["crop_resolution"] = {
        "source": "未生成",
        "method": "需要教师确认页码/题号或手动框选",
        "error": "；".join(errors) if errors else "缺少可用的 PDF 裁切依据",
    }
    return None
