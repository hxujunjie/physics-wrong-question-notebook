"""教师端两步人工复核的数据模型与持久化服务。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


SCHEMA_VERSION = 3
MIN_ALIGNMENT_CONFIDENCE = 0.20


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def evidence_id(student: str, photo_sha256: str, source_question_id: str) -> str:
    raw = f"{student}\0{photo_sha256}\0{source_question_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _transform_norm(points, H, width: int, height: int):
    if H is None or width <= 0 or height <= 0:
        return None
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    try:
        mapped = cv2.perspectiveTransform(pts, np.asarray(H, dtype=np.float64)).reshape(-1, 2)
    except (cv2.error, ValueError, TypeError):
        return None
    if not np.isfinite(mapped).all():
        return None
    normalized = [[float(x) / width, float(y) / height] for x, y in mapped]
    if any(x < -0.03 or x > 1.03 or y < -0.03 or y > 1.03 for x, y in normalized):
        return None
    return [[round(min(1.0, max(0.0, x)), 6), round(min(1.0, max(0.0, y)), 6)] for x, y in normalized]


def reverse_map_question(question, H_photo_to_pdf, photo_shape, alignment_confidence: float, reliable: bool):
    """把 PDF 题号与题块反向映射到校正照片的归一化坐标。"""
    if not reliable or alignment_confidence < MIN_ALIGNMENT_CONFIDENCE or H_photo_to_pdf is None:
        return None, None
    try:
        inverse = np.linalg.inv(np.asarray(H_photo_to_pdf, dtype=np.float64))
    except (np.linalg.LinAlgError, ValueError, TypeError):
        return None, None
    height, width = photo_shape[:2]
    x0, y0, x1, y1 = question.bbox
    anchor = _transform_norm([[(x0 + x1) / 2, (y0 + y1) / 2]], inverse, width, height)
    polygon = _transform_norm(
        [[question.column_x0 or x0, question.top_y],
         [question.column_x1 or x1, question.top_y],
         [question.column_x1 or x1, question.bottom_y],
         [question.column_x0 or x0, question.bottom_y]],
        inverse, width, height,
    )
    return (anchor[0] if anchor else None), polygon


def build_page_review_questions(source_index, page_index: int, result: dict, H_photo_to_pdf, photo_shape) -> list[dict]:
    """生成匹配页全部正式题目的审核列表，保持练习册阅读顺序。"""
    questions = sorted(
        source_index.page_questions(page_index),
        key=lambda q: (q.column if q.column >= 0 else 0, q.top_y, q.bbox[0]),
    )
    candidates = {str(c.get("source_question_id")): c for c in result.get("candidate_questions", [])}
    reviews = {str(c.get("source_question_id")): c for c in result.get("review_questions", [])}
    marks = result.get("marks", [])
    reliable = bool(result.get("registration_reliable", result.get("alignment", {}).get("success", False)))
    alignment_confidence = float(result.get("alignment_confidence") or result.get("alignment", {}).get("confidence") or 0)
    items = []
    for order, question in enumerate(questions):
        qid = str(question.question_id)
        candidate = candidates.get(qid)
        pending = reviews.get(qid)
        mark_indices = []
        if candidate is not None and isinstance(candidate.get("source_mark_idx"), int):
            mark_indices.append(candidate["source_mark_idx"])
        if pending is not None and isinstance(pending.get("source_mark_idx"), int):
            mark_indices.append(pending["source_mark_idx"])
        mark_types = [marks[i].get("type", "unknown") for i in mark_indices if 0 <= i < len(marks)]
        confidences = [float(marks[i].get("confidence") or 0) for i in mark_indices if 0 <= i < len(marks)]
        requires_review = pending is not None or any(t == "unknown" for t in mark_types)
        if requires_review:
            suggested = "uncertain"
            decision = None
            reason = (pending or {}).get("reason") or "标记或题目边界置信度不足"
        elif candidate is not None:
            suggested = "wrong"
            decision = "wrong"
            reason = "检测到高置信度错误标记"
        else:
            suggested = "correct"
            decision = "correct"
            reason = "未检测到错误标记"
        anchor, polygon = reverse_map_question(
            question, H_photo_to_pdf, photo_shape, alignment_confidence, reliable
        )
        try:
            segments = source_index.question_segments(question)
        except Exception:
            segments = []
        complete = bool(segments)
        items.append({
            "source_question_id": qid,
            "qnum": str(question.qnum),
            "page_index": page_index,
            "reading_order": order,
            "segments": [{"page_index": s.page_index, "bbox": list(s.bbox), "is_continuation": bool(s.is_continuation)} for s in segments],
            "suggested_decision": suggested,
            "decision": decision,
            "confidence": round(min(confidences) if confidences else (1.0 if suggested == "correct" else 0.8), 4),
            "requires_review": requires_review,
            "reason": reason,
            "mark_indices": mark_indices,
            "mark_types": mark_types,
            "photo_anchor_norm": anchor,
            "photo_polygon_norm": polygon,
            "content_complete": complete,
            "content_error": None if complete else "无法获得完整题干、图片、选项或续页",
            "teacher_modified": False,
            "viewed": False,
        })
    return items


def load_manifest(path: str | Path) -> dict:
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: str | Path, manifest: dict) -> None:
    path = Path(path)
    manifest["schema_version"] = SCHEMA_VERSION
    manifest["last_modified_at"] = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def set_decision(manifest: dict, evidence: str, decision: str) -> dict:
    if decision not in {"wrong", "correct", "pending"}:
        raise ValueError("decision 必须是 wrong、correct 或 pending")
    for page in manifest.get("photo_tasks", []):
        for question in page.get("page_review_questions", []):
            if question.get("evidence_id") == evidence:
                previous = question.get("decision")
                question["decision"] = None if decision == "pending" else decision
                question["teacher_modified"] = decision != "pending" and decision != question.get("suggested_decision")
                question["viewed"] = True
                if question.get("ai_result") is not None:
                    question.setdefault("field_sources", {})["decision"] = "teacher" if decision != "pending" else "ai"
                    question.setdefault("modification_history", []).append({"at": now_iso(), "field": "decision", "from": previous, "to": question["decision"], "actor": "teacher"})
                manifest["pdf_dirty"] = True
                return question
    raise KeyError(f"未找到 evidence_id: {evidence}")


def evidence_counts(manifest: dict) -> dict[str, int]:
    counts = {str(k): int(v) for k, v in manifest.get("legacy_baseline", {}).items()}
    for page in manifest.get("photo_tasks", []):
        for question in page.get("page_review_questions", []):
            if question.get("decision") == "wrong" and question.get("content_complete"):
                qid = str(question.get("source_question_id"))
                counts[qid] = counts.get(qid, 0) + 1
    return counts


def page_can_complete(page: dict) -> tuple[bool, int]:
    remaining = sum(1 for q in page.get("page_review_questions", []) if q.get("requires_review") and q.get("decision") is None)
    return remaining == 0, remaining


def mark_page_complete(page: dict, allow_unresolved: bool = False) -> None:
    allowed, remaining = page_can_complete(page)
    if not allowed and not allow_unresolved:
        raise ValueError(f"仍有 {remaining} 道低置信题未判断")
    for question in page.get("page_review_questions", []):
        question["viewed"] = True
    page["review_completed"] = True
    page["review_completed_at"] = now_iso()
    if remaining:
        page["review_completed_with_unresolved"] = remaining


def update_question(manifest: dict, evidence: str, *, qnum: str | None = None) -> dict:
    """Apply a teacher question-number correction and retain its provenance."""
    for page in manifest.get("photo_tasks", []):
        for question in page.get("page_review_questions", []):
            if question.get("evidence_id") != evidence:
                continue
            cleaned = str(qnum).strip() if qnum is not None else ""
            if not cleaned:
                raise ValueError("题号不能为空")
            old = question.get("qnum")
            question["qnum"] = cleaned
            # Keep crop_spec in sync so export does not keep using a stale number.
            spec = question.get("crop_spec") if isinstance(question.get("crop_spec"), dict) else {}
            question["crop_spec"] = spec
            spec["question_no"] = cleaned
            page_index = spec.get("pdf_page_index_0based")
            if not isinstance(page_index, int):
                page_index = question.get("page_index")
            has_manual = isinstance(spec.get("manual_segments"), list) and bool(spec.get("manual_segments"))
            if has_manual:
                question["crop_source"] = "原始PDF"
                question["crop_method"] = "教师手动框选"
                question["crop_action"] = "manual_done"
                question["crop_hint"] = "已手动框选"
                question["content_complete"] = True
                question["content_error"] = None
            elif isinstance(page_index, int) and cleaned:
                # Prefer page+qnum index after a teacher number correction.
                spec["source"] = "pdf_index"
                if "pdf_page_index_0based" not in spec:
                    spec["pdf_page_index_0based"] = page_index
                question["crop_source"] = "原始PDF"
                question["crop_method"] = "页码+题号索引"
                question["crop_action"] = "auto"
                question["crop_hint"] = "可自动裁切（页码+题号）"
                question["content_complete"] = True
                question["content_error"] = None
            elif isinstance(page_index, int):
                spec["source"] = "pdf_pending"
                question["crop_source"] = "待定位"
                question["crop_method"] = "缺少题号"
                question["crop_action"] = "fix_qnum"
                question["crop_hint"] = "请先填写题号；若仍失败再手动框选"
                question["content_complete"] = False
                question["content_error"] = question["crop_hint"]
            else:
                spec["source"] = "pdf_pending"
                question["crop_source"] = "待定位"
                question["crop_method"] = "等待教师确认 PDF 页码/题号"
                question["crop_action"] = "manual_or_page"
                question["crop_hint"] = "页码不可靠：请确认 PDF 页或手动框选"
                question["content_complete"] = False
                question["content_error"] = question["crop_hint"]
            question["teacher_modified"] = True
            question.setdefault("field_sources", {})["qnum"] = "teacher"
            question.setdefault("modification_history", []).append({"at": now_iso(), "field": "qnum", "from": old, "to": cleaned, "actor": "teacher"})
            manifest["pdf_dirty"] = True
            return question
    raise KeyError(f"未找到 evidence_id: {evidence}")


def _normalize_display_rotation(value: object) -> int:
    try:
        degrees = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("旋转角度必须是 0/90/180/270") from exc
    degrees %= 360
    if degrees not in {0, 90, 180, 270}:
        raise ValueError("旋转角度必须是 0/90/180/270")
    return degrees


def _rotate_norm_point(x: float, y: float, degrees: int) -> list[float]:
    """Map a normalized photo point when the image is rotated clockwise for display."""
    xx, yy = float(x), float(y)
    if degrees == 90:
        return [round(1.0 - yy, 6), round(xx, 6)]
    if degrees == 180:
        return [round(1.0 - xx, 6), round(1.0 - yy, 6)]
    if degrees == 270:
        return [round(yy, 6), round(1.0 - xx, 6)]
    return [round(xx, 6), round(yy, 6)]




def sync_photo_task_pdf_page(manifest: dict, photo_sha256: str, page_index_0based: int, *, actor: str = "teacher_page_sync") -> dict:
    """Correct the matched PDF page for one photo task and propagate to sibling questions.

    Returns page, previous/new page numbers, and how many sibling questions changed.
    """
    if not isinstance(page_index_0based, int) or page_index_0based < 0:
        raise ValueError("PDF 页码无效")
    target = None
    for page in manifest.get("photo_tasks", []):
        if str(page.get("photo_sha256") or "") == str(photo_sha256 or ""):
            target = page
            break
    if target is None:
        raise KeyError(f"未找到 photo_sha256: {photo_sha256}")

    previous = target.get("matched_pdf_page_index_0based")
    if not isinstance(previous, int):
        # fall back to first question page if page-level match missing
        for q in target.get("page_review_questions", []):
            if isinstance(q.get("page_index"), int):
                previous = q.get("page_index")
                break

    page_changed = previous != page_index_0based
    target["matched_pdf_page_index_0based"] = page_index_0based
    target["matched_pdf_page"] = page_index_0based + 1
    target["registration_reliable"] = True
    target["match_review_reason"] = ""
    target["teacher_page_corrected"] = True
    target["teacher_page_corrected_at"] = now_iso()

    synced_count = 0
    for question in target.get("page_review_questions", []):
        spec = question.get("crop_spec") if isinstance(question.get("crop_spec"), dict) else {}
        question["crop_spec"] = spec
        has_manual = (
            spec.get("source") == "pdf_manual"
            or (isinstance(spec.get("manual_segments"), list) and bool(spec.get("manual_segments")))
        )
        old_page = spec.get("pdf_page_index_0based")
        if not isinstance(old_page, int):
            old_page = question.get("page_index")

        question["page_index"] = page_index_0based
        spec["pdf_page_index_0based"] = page_index_0based
        if question.get("qnum"):
            spec["question_no"] = question.get("qnum")

        if has_manual:
            # keep manual geometry; only page metadata updates
            pass
        else:
            spec["source"] = "pdf_index"
            question["crop_source"] = "原始PDF"
            question["crop_method"] = "页码+题号索引"
            question["crop_action"] = "auto"
            question["crop_hint"] = f"可自动裁切（PDF 第 {page_index_0based + 1} 页）"
            question["content_complete"] = True
            question["content_error"] = None
            question["crop_resolution"] = None

        if old_page != page_index_0based:
            question.setdefault("modification_history", []).append({
                "at": now_iso(),
                "field": "page_index",
                "from": old_page,
                "to": page_index_0based,
                "actor": actor,
            })
            synced_count += 1

    manifest["pdf_dirty"] = True
    manifest["last_modified_at"] = now_iso()
    return {
        "page": target,
        "previous_page_index_0based": previous,
        "pdf_page_index_0based": page_index_0based,
        "pdf_page": page_index_0based + 1,
        "page_changed": bool(page_changed),
        "synced_count": synced_count,
        "pdf_dirty": True,
    }

def apply_manual_crop(
    manifest: dict,
    evidence_id: str,
    segments: list[dict],
    *,
    sync_page_siblings: bool = True,
) -> dict:
    """Save a teacher PDF crop and optionally sync corrected page to sibling questions.

    When the first crop segment lands on a different PDF page than the photo was
    matched to, treat it as a page correction for the whole photo task:
    - update page-level matched_pdf_page*
    - update other non-manual questions to the new page + keep their qnum for re-crop
    Manual segments already drawn on other questions are preserved.
    """
    if not isinstance(segments, list) or not segments:
        raise ValueError("至少需要一个 PDF 框选片段")
    first_page = segments[0].get("page_index")
    if not isinstance(first_page, int) or first_page < 0:
        raise ValueError("框选页码无效")

    target_page = None
    target_question = None
    for page in manifest.get("photo_tasks", []):
        for question in page.get("page_review_questions", []):
            if question.get("evidence_id") == evidence_id:
                target_page = page
                target_question = question
                break
        if target_question is not None:
            break
    if target_question is None or target_page is None:
        raise KeyError(f"未找到 evidence_id: {evidence_id}")

    old_spec = target_question.get("crop_spec") or {}
    previous_page = old_spec.get("pdf_page_index_0based")
    if not isinstance(previous_page, int):
        previous_page = target_question.get("page_index")
    if not isinstance(previous_page, int):
        previous_page = target_page.get("matched_pdf_page_index_0based")

    target_question["crop_spec"] = {
        **old_spec,
        "source": "pdf_manual",
        "manual_segments": segments,
        "question_no": target_question.get("qnum"),
        "pdf_page_index_0based": first_page,
    }
    target_question.update(
        page_index=first_page,
        crop_source="原始PDF",
        crop_method="教师手动框选",
        crop_action="manual_done",
        crop_hint="已手动框选",
        content_complete=True,
        content_error=None,
        teacher_modified=True,
    )
    target_question.setdefault("field_sources", {})["crop_source"] = "teacher"
    target_question.setdefault("modification_history", []).append({
        "at": now_iso(),
        "field": "crop_source",
        "from": old_spec.get("source"),
        "to": "pdf_manual",
        "actor": "teacher",
    })
    if previous_page != first_page:
        target_question.setdefault("modification_history", []).append({
            "at": now_iso(),
            "field": "page_index",
            "from": previous_page,
            "to": first_page,
            "actor": "teacher",
        })

    page_changed = previous_page != first_page
    synced_count = 0
    if sync_page_siblings and page_changed:
        sync = sync_photo_task_pdf_page(
            manifest,
            str(target_page.get("photo_sha256") or ""),
            first_page,
            actor="teacher_page_sync",
        )
        # re-bind after sync mutation
        target_page = sync["page"]
        # current question already set to manual crop; ensure it still wins
        for question in target_page.get("page_review_questions", []):
            if question.get("evidence_id") == evidence_id:
                target_question = question
                break
        # synced_count includes current question page change; exclude it for notice clarity
        synced_count = max(0, int(sync.get("synced_count") or 0) - 1)

    manifest["pdf_dirty"] = True
    manifest["last_modified_at"] = now_iso()
    return {
        "question": target_question,
        "page": target_page,
        "synced_count": synced_count,
        "page_changed": bool(page_changed),
        "pdf_page": first_page + 1,
        "pdf_dirty": True,
    }


def set_photo_display_rotation(manifest: dict, photo_sha256: str, degrees: int) -> dict:
    """Persist a per-photo display rotation so landscape shots can be reviewed upright.

    Only affects review presentation (image + overlay). Export still uses the clean PDF.
    """
    target = str(photo_sha256 or "")
    if not target:
        raise ValueError("缺少 photo_sha256")
    rotation = _normalize_display_rotation(degrees)
    for page in manifest.get("photo_tasks", []):
        if str(page.get("photo_sha256") or "") != target:
            continue
        previous = _normalize_display_rotation(page.get("display_rotation_deg") or 0)
        page["display_rotation_deg"] = rotation
        page["display_rotation_updated_at"] = now_iso()
        if previous != rotation:
            page.setdefault("modification_history", []).append({
                "at": now_iso(),
                "field": "display_rotation_deg",
                "from": previous,
                "to": rotation,
                "actor": "teacher",
            })
        manifest["last_modified_at"] = now_iso()
        return page
    raise KeyError(f"未找到 photo_sha256: {photo_sha256}")


def migrate_legacy_pending(manifest: dict, pending_path: str | Path) -> int:
    path = Path(pending_path)
    if not path.exists():
        return 0
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    existing = {q.get("source_question_id") for p in manifest.get("photo_tasks", []) for q in p.get("page_review_questions", [])}
    added = 0
    for index, old in enumerate(records if isinstance(records, list) else []):
        qid = str(old.get("source_question_id") or f"legacy-{index + 1}")
        if qid in existing:
            continue
        files = old.get("candidate_files") or []
        manifest.setdefault("photo_tasks", []).append({
            "photo_path": old.get("photo_path"), "photo_name": old.get("photo_name") or "历史待确认",
            "rectified_photo": None, "legacy_preview": files[0] if files else None,
            "matched_pdf_page": None, "registration_confidence": 0.0, "review_completed": False,
            "historical_without_full_page": True,
            "page_review_questions": [{
                "source_question_id": qid, "qnum": str(old.get("qnum") or "?"), "page_index": old.get("page_index"),
                "reading_order": 0, "segments": [], "suggested_decision": "uncertain", "decision": None,
                "confidence": float(old.get("confidence") or 0), "requires_review": True,
                "reason": old.get("reason") or "旧待确认记录", "mark_indices": [], "mark_types": [],
                "photo_anchor_norm": None, "photo_polygon_norm": None, "content_complete": bool(files),
                "content_error": None if files else "历史记录没有完整题目内容", "teacher_modified": False, "viewed": False,
            }],
        })
        existing.add(qid)
        added += 1
    return added
