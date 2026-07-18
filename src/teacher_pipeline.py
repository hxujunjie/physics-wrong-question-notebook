"""AI 导入模式的教师复核与干净 PDF 交付流水线。

学生照片只用于教师查看和标记定位；最终错题集只允许来自所选干净 PDF。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import config, ocr_questions, recognition_import, review_workspace, wrong_book_pdf

PIPELINE_VERSION = "teacher-review-6-confirm-then-export"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_review_decision(output_root: str | Path, student: str, evidence_id: str, decision: str) -> dict:
    manifest_path = Path(output_root) / "_cache" / student / "review_manifest.json"
    manifest = review_workspace.load_manifest(manifest_path)
    question = review_workspace.set_decision(manifest, evidence_id, decision)
    review_workspace.save_manifest(manifest_path, manifest)
    return question


def complete_review_page(output_dir: str | Path, student: str, photo_sha256: str, allow_unresolved: bool = False) -> dict:
    path = Path(output_dir) / "_cache" / student / "review_manifest.json"
    manifest = review_workspace.load_manifest(path)
    page = next(p for p in manifest.get("photo_tasks", []) if p.get("photo_sha256") == photo_sha256)
    review_workspace.mark_page_complete(page, allow_unresolved=allow_unresolved)
    review_workspace.save_manifest(path, manifest)
    return page


def _manual_segments(manifest: dict, question: dict, source_index: ocr_questions.SourceBookIndex):
    spec = question.get("crop_spec") or {}
    raw = spec.get("manual_segments")
    if not isinstance(raw, list) or not raw:
        return None, None
    segments: list[ocr_questions.SourceSegment] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        page_index = item.get("page_index")
        bbox = item.get("bbox_norm")
        if not isinstance(page_index, int) or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        source_index.ensure_pages([page_index])
        image = source_index.images.get(page_index)
        if image is None:
            continue
        h, w = image.shape[:2]
        x0, y0, x1, y1 = [float(value) for value in bbox]
        segments.append(ocr_questions.SourceSegment(page_index, (round(x0*w), round(y0*h), round(x1*w), round(y1*h)), bool(item.get("is_continuation", False))))
    if not segments:
        return None, "教师手动框选的 PDF 片段无效"
    images = source_index.extract_segments(segments)
    if len(images) != len(segments) or any(image is None or image.size == 0 for image in images):
        return None, "教师手动框选的 PDF 片段无法读取"
    return [wrong_book_pdf.QuestionSegment(s.page_index, image, s.is_continuation) for s, image in zip(segments, images)], None


def _strict_pdf_segments(manifest: dict, question: dict, source_index: ocr_questions.SourceBookIndex):
    """Resolve an AI question to clean-PDF segments; never use a photo crop.

    Priority:
    1. Teacher manual crop
    2. Clean-PDF page + question number index (most reliable for workbooks)
    3. AI reference_bbox only as fallback — free VL models often shift the box
       by one question (e.g. label says 5 but box covers 4).
    """
    spec = question.get("crop_spec") or {}
    page_index = spec.get("pdf_page_index_0based")
    if not isinstance(page_index, int):
        page_index = question.get("page_index")
    # Prefer the live teacher-facing qnum over a stale crop_spec.question_no.
    qnum = str(question.get("qnum") or spec.get("question_no") or "").strip()

    manual, manual_error = _manual_segments(manifest, question, source_index)
    if manual is not None:
        return manual, None, "原始PDF（教师手动框选）"

    errors = [manual_error] if manual_error else []
    if isinstance(page_index, int) and qnum:
        resolved, error = recognition_import._resolve_pdf_index(
            manifest,
            {"pdf_page_index_0based": page_index, "question_no": qnum},
            source_index,
        )
        if resolved is not None:
            segments, images = resolved
            return (
                [wrong_book_pdf.QuestionSegment(s.page_index, image, s.is_continuation) for s, image in zip(segments, images)],
                None,
                "原始PDF（页码+题号索引）",
            )
        errors.append(error or "PDF 页码+题号索引失败")
    else:
        errors.append("缺少有效的 PDF 页码或题号")

    ref_bbox = spec.get("reference_bbox")
    if isinstance(page_index, int) and isinstance(ref_bbox, list) and len(ref_bbox) == 4:
        temporary = dict(
            question,
            crop_spec={
                "source": "pdf_bbox",
                "reference_bbox": ref_bbox,
                "pdf_page_index_0based": page_index,
            },
        )
        image = recognition_import.crop_imported_question(manifest, {}, temporary, source_index)
        if image is not None and image.size:
            return [wrong_book_pdf.QuestionSegment(page_index, image, False)], None, "原始PDF（reference_bbox 兜底）"
        errors.append("reference_bbox 裁切失败")

    return None, "；".join(errors), "待处理（未生成）"


def rebuild_student_pdf(output_dir: str | Path, student: str) -> dict:
    output = Path(output_dir)
    manifest_path = output / "_cache" / student / "review_manifest.json"
    manifest = review_workspace.load_manifest(manifest_path)
    clean_pdf = Path(manifest["clean_pdf_path"])
    source_index = ocr_questions.SourceBookIndex(clean_pdf, dpi=config.PDF_RENDER_DPI)
    selected: dict[str, dict] = {}
    for page in manifest.get("photo_tasks", []):
        for question in page.get("page_review_questions", []):
            if question.get("decision") != "wrong":
                continue
            qid = str(question.get("source_question_id") or question.get("evidence_id"))
            selected.setdefault(qid, {"question": question, "occurrences": []})["occurrences"].append(question.get("evidence_id"))

    items = []
    pending = []
    for entry in selected.values():
        question = entry["question"]
        segments, error, source = _strict_pdf_segments(manifest, question, source_index)
        if segments is None:
            question["content_complete"] = False
            question["content_error"] = error
            question["crop_source"] = source
            question["crop_resolution"] = {"source": source, "method": "未生成", "error": error}
            pending.append({"evidence_id": question.get("evidence_id"), "qnum": question.get("qnum"), "reason": error, "source": source})
            continue
        question["content_complete"] = True
        question["content_error"] = None
        question["crop_source"] = "原始PDF"
        question["crop_resolution"] = {"source": source, "method": source, "error": None}
        items.append(wrong_book_pdf.WrongBookItem(qnum=question.get("qnum"), mark_type="cross", page_index=question.get("page_index"), source_text=None, occurrence_count=len(entry["occurrences"]), segments=segments, source_question_id=question.get("source_question_id")))

    student_dir = output / student
    student_dir.mkdir(parents=True, exist_ok=True)
    for child in student_dir.iterdir():
        if child.is_file() and child.suffix.lower() != ".pdf":
            child.unlink()
    pdf_path = student_dir / f"{student}_物理错题集.pdf"
    if items:
        wrong_book_pdf.generate_wrong_book_pdf(items, str(pdf_path), student_name=student, title=f"{student} - 物理错题集")
    elif pdf_path.exists():
        pdf_path.unlink()
    manifest["pdf_dirty"] = False
    manifest["last_rebuilt_at"] = review_workspace.now_iso()
    manifest["content_pending"] = pending
    review_workspace.save_manifest(manifest_path, manifest)
    return {"student": student, "pdf": str(pdf_path) if pdf_path.exists() else None, "wrong_question_count": len(items), "content_pending": pending}


def _existing_student_pdfs(output: Path) -> list[dict]:
    """List already-exported student PDFs without rebuilding."""
    found = []
    cache = output / "_cache"
    if not cache.is_dir():
        return found
    for path in sorted(cache.glob("*/review_manifest.json")):
        student = path.parent.name
        pdf_path = output / student / f"{student}_物理错题集.pdf"
        if pdf_path.is_file():
            found.append({"student": student, "pdf": str(pdf_path), "existing": True})
    return found


def finalize_delivery(output_dir: str | Path, allow_incomplete: bool = False) -> dict:
    output = Path(output_dir)
    manifests = sorted((output / "_cache").glob("*/review_manifest.json"))
    incomplete_pages = []
    incomplete_students = set()
    for path in manifests:
        manifest = review_workspace.load_manifest(path)
        for page in manifest.get("photo_tasks", []):
            if not page.get("review_completed"):
                incomplete_pages.append((manifest.get("student"), page.get("photo_name")))
                incomplete_students.add(manifest.get("student"))

    # Do not build/overwrite PDFs while the teacher is still blocked by unfinished
    # pages; otherwise pdf_dirty is cleared and a later successful export reports 0.
    if incomplete_pages and not allow_incomplete:
        return {
            "status": "review_required",
            "unreviewed_student_count": len(incomplete_students),
            "unreviewed_page_count": len(incomplete_pages),
            "rebuilt": [],
            "existing_pdfs": _existing_student_pdfs(output),
        }

    rebuilt = []
    for path in manifests:
        manifest = review_workspace.load_manifest(path)
        # Rebuild only when decisions/crops changed, or for legacy import sources
        # that historically required a first-pass export without an explicit dirty flag.
        import_source = str(manifest.get("import_source") or "")
        if manifest.get("pdf_dirty") or import_source in {"doubao_json", "recognition_json"}:
            rebuilt.append(rebuild_student_pdf(output, manifest["student"]))
        else:
            # Already up to date: still surface the existing PDF in the result list.
            student = manifest.get("student") or path.parent.name
            pdf_path = output / student / f"{student}_物理错题集.pdf"
            if pdf_path.is_file():
                rebuilt.append({
                    "student": student,
                    "pdf": str(pdf_path),
                    "wrong_question_count": None,
                    "content_pending": manifest.get("content_pending") or [],
                    "existing": True,
                })

    summary_path = next(iter(output.glob("*汇总*.json")), output / "班级汇总.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    summary.update({
        "unreviewed_student_count": len(incomplete_students),
        "unreviewed_page_count": len(incomplete_pages),
        "finalized_at": review_workspace.now_iso(),
    })
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "success",
        "unreviewed_student_count": len(incomplete_students),
        "unreviewed_page_count": len(incomplete_pages),
        "rebuilt": rebuilt,
    }
