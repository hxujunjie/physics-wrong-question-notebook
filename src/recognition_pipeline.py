"""Recoverable multi-vendor recognition pipeline for the teacher browser app."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import fitz
import numpy as np

from . import ai_settings, recognition_import, render_pdf
from .ai_client import AiClient


SCHEMA_VERSION = "1.1"
PROMPT_VERSION = "teacher-marks-v1"
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png"}
PAGE_BATCH_SIZE = 2  # smaller batches = fewer tokens/request; better for free-tier rate limits
LOW_CONFIDENCE = 0.80


class BudgetExceeded(RuntimeError):
    """Raised before a new request when the teacher-approved call budget is exhausted."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _image_bytes(image: np.ndarray, *, limit: int = 2200) -> bytes:
    height, width = image.shape[:2]
    if max(height, width) > limit:
        factor = limit / max(height, width)
        image = cv2.resize(image, (round(width * factor), round(height * factor)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise ValueError("图片压缩失败")
    if len(encoded) > 20 * 1024 * 1024:
        raise ValueError("图片压缩后仍超过 20 MiB 限制")
    return encoded.tobytes()


def _exif_orientation(path: Path) -> int:
    """Return EXIF orientation tag (1..8). Missing/unreadable EXIF => 1 (upright)."""
    try:
        from PIL import Image, ExifTags
    except Exception:
        return 1
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            if not exif:
                return 1
            orientation_key = next((k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None)
            if orientation_key is None:
                return 1
            value = int(exif.get(orientation_key) or 1)
            return value if 1 <= value <= 8 else 1
    except Exception:
        return 1


def _apply_exif_orientation(image: np.ndarray, orientation: int) -> np.ndarray:
    """Rotate/flip BGR image so it matches human-upright viewing."""
    if orientation <= 1 or image is None:
        return image
    # Common phone values: 3=180, 6=90 CW, 8=270 CW (90 CCW).
    if orientation == 2:
        return cv2.flip(image, 1)
    if orientation == 3:
        return cv2.rotate(image, cv2.ROTATE_180)
    if orientation == 4:
        return cv2.flip(image, 0)
    if orientation == 5:
        return cv2.rotate(cv2.flip(image, 1), cv2.ROTATE_90_COUNTERCLOCKWISE)
    if orientation == 6:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if orientation == 7:
        return cv2.rotate(cv2.flip(image, 1), cv2.ROTATE_90_CLOCKWISE)
    if orientation == 8:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image


def _upright_photo_image(path: Path) -> tuple[np.ndarray, dict]:
    """Load a student photo already rotated to upright for recognition/review."""
    orientation = _exif_orientation(path)
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("图片无法读取")
    upright = _apply_exif_orientation(image, orientation)
    meta = {
        "exif_orientation": orientation,
        "auto_rotated": upright is not image and orientation not in {0, 1},
        "source_size": [int(image.shape[1]), int(image.shape[0])],
        "upright_size": [int(upright.shape[1]), int(upright.shape[0])],
    }
    return upright, meta


def prepare_review_photo(path: str | Path, output_dir: str | Path, student: str, photo_sha256: str) -> dict:
    """Write an upright JPEG for review and return display/path metadata.

    Original files remain untouched. Review uses the upright derivative so teachers
    and overlays see the same orientation the model saw.
    """
    source = Path(path)
    upright, meta = _upright_photo_image(source)
    target_dir = Path(output_dir) / "_cache" / student / "upright_photos"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{photo_sha256}.jpg"
    ok, encoded = cv2.imencode(".jpg", upright, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise ValueError("校正照片写入失败")
    encoded.tofile(str(target))
    return {
        "rectified_photo": str(target.resolve()),
        "display_rotation_deg": 0,
        "orientation_meta": meta,
        "auto_upright": True,
    }


def _read_photo(path: Path) -> bytes:
    image, _meta = _upright_photo_image(path)
    return _image_bytes(image)


def _render_page(pdf_path: Path, page_index: int) -> bytes:
    return _image_bytes(render_pdf.render_page(pdf_path, page_index, dpi=160))


def cache_root() -> Path:
    """Primary durable cache under the current Windows user profile."""
    root = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("USERPROFILE") or ".")
    return root / "PhysicsWrongBook" / "cache" / "pdf-index"


def project_cache_root() -> Path:
    """Project-local fallback cache so indexing survives AppData cleanup/moves."""
    return Path(__file__).resolve().parents[1] / "data" / "pdf-index-cache"


def _cache_dirs() -> list[Path]:
    dirs: list[Path] = []
    for path in (cache_root(), project_cache_root()):
        if path not in dirs:
            dirs.append(path)
    return dirs


def _pdf_index_filename(pdf_hash: str) -> str:
    # Content-hash only: the same clean workbook should reuse the page/question
    # index across providers/models. Prompt version stays in the payload for
    # compatibility checks.
    return f"{pdf_hash}-{PROMPT_VERSION}.json"


def _validate_pdf_index_payload(payload: dict, *, pdf_hash: str, page_count: int | None = None) -> list[dict] | None:
    if not isinstance(payload, dict):
        return None
    version = payload.get("prompt_version")
    if version is not None and version != PROMPT_VERSION:
        # Different prompt versions are ignored rather than trusted blindly.
        return None
    if payload.get("pdf_sha256") and payload.get("pdf_sha256") != pdf_hash:
        return None
    pages = payload.get("pages")
    if not isinstance(pages, list) or not pages:
        return None
    cleaned: list[dict] = []
    for item in pages:
        if not isinstance(item, dict):
            continue
        try:
            page_number = int(item.get("page_number"))
        except (TypeError, ValueError):
            continue
        if page_number < 1:
            continue
        questions = []
        for question in item.get("questions") or []:
            if not isinstance(question, dict):
                continue
            number = str(question.get("question_no") or "").strip()
            box = _bbox(question.get("bbox"))
            if number and box:
                questions.append({"question_no": number, "bbox": box})
        cleaned.append(
            {
                "page_number": page_number,
                "anchor_text": str(item.get("anchor_text") or ""),
                "questions": questions,
            }
        )
    if not cleaned:
        return None
    cleaned.sort(key=lambda item: item["page_number"])
    if page_count is not None and page_count > 0:
        # Require a complete index for the current workbook length.
        numbers = {item["page_number"] for item in cleaned}
        if any(page not in numbers for page in range(1, page_count + 1)):
            return None
    return cleaned


def load_pdf_index_cache(
    pdf_hash: str,
    *,
    page_count: int | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict | None:
    """Load a reusable clean-PDF page index.

    Lookup order:
    1. New content-hash cache in AppData / project data
    2. Legacy provider/model-bound filenames for migration
    """
    names = [_pdf_index_filename(pdf_hash)]
    if provider and model:
        names.append(f"{pdf_hash}-{provider}-{model}-{PROMPT_VERSION}.json")

    for directory in _cache_dirs():
        for name in names:
            path = directory / name
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            pages = _validate_pdf_index_payload(payload, pdf_hash=pdf_hash, page_count=page_count)
            if pages is None:
                continue
            return {
                "pages": pages,
                "path": str(path),
                "complete": True,
                "provider": payload.get("provider"),
                "model": payload.get("model"),
            }
    return None


def save_pdf_index_cache(
    pdf_hash: str,
    pages: list[dict],
    *,
    provider: str,
    model: str,
    page_count: int | None = None,
) -> list[Path]:
    """Persist the clean-PDF index to all cache roots. Best-effort per root."""
    payload = {
        "pdf_sha256": pdf_hash,
        "provider": provider,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "page_count": page_count if page_count is not None else len(pages),
        "complete": True,
        "pages": pages,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    written: list[Path] = []
    name = _pdf_index_filename(pdf_hash)
    for directory in _cache_dirs():
        path = directory / name
        try:
            _atomic_json(path, payload)
            written.append(path)
        except OSError:
            continue
    return written


def estimate_index_calls(pdf_path: str | Path, *, provider: str | None = None, model: str | None = None) -> tuple[int, bool, int]:
    """Return (index_api_calls, cache_hit, page_count) for preflight/budget."""
    pdf = Path(pdf_path).expanduser().resolve()
    with fitz.open(pdf) as document:
        page_count = document.page_count
    pdf_hash = _sha256(pdf)
    cached = load_pdf_index_cache(pdf_hash, page_count=page_count, provider=provider, model=model)
    if cached is not None:
        return 0, True, page_count
    index_calls = (page_count + PAGE_BATCH_SIZE - 1) // PAGE_BATCH_SIZE
    return index_calls, False, page_count


def discover_students(photo_root: str | Path) -> list[dict]:
    root = Path(photo_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("学生照片总目录不存在")
    students: list[dict] = []
    direct_dirs = [path for path in sorted(root.iterdir()) if path.is_dir()]
    for folder in direct_dirs:
        photos = [path for path in sorted(folder.rglob("*")) if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS]
        if photos:
            students.append({"student": folder.name, "photos": [str(path) for path in photos]})
    loose = [path for path in sorted(root.iterdir()) if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS]
    if loose:
        students.append({"student": "未分组照片", "photos": [str(path) for path in loose]})
    if not students:
        raise ValueError("未在学生子文件夹中找到 JPG 或 PNG 照片")
    return students


def make_output_dir(output_root: str | Path, pdf_path: str | Path) -> Path:
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[<>:"/\\|?*]+', "_", Path(pdf_path).stem).strip(" .") or "错题整理"
    base = root / f"{datetime.now():%Y%m%d-%H%M}_{safe_name}"
    candidate, index = base, 2
    while candidate.exists():
        candidate = root / f"{base.name}_{index}"
        index += 1
    candidate.mkdir()
    return candidate


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if any(item < 0 for item in result) or result[0] >= result[2] or result[1] >= result[3]:
        return None
    # Free VL models often return pixel or 0~1000 coords; convert to 0~1.
    if any(item > 1 for item in result):
        peak = max(result)
        scale = 100.0 if peak <= 100 else 1000.0 if peak <= 1000 else max(result[2], result[3], 1.0)
        result = [item / scale for item in result]
    result = [round(min(1.0, max(0.0, item)), 6) for item in result]
    if result[0] >= result[2] or result[1] >= result[3]:
        return None
    return result


def _tokens(value: str) -> set[str]:
    return {piece for piece in re.split(r"[^\w\u4e00-\u9fff]+", value.lower()) if len(piece) > 1}


def _match_page(photo: dict, index: list[dict]) -> tuple[dict | None, float, list[dict]]:
    numbers = {str(question.get("question_no", "")).strip() for question in photo.get("visible_questions", [])}
    anchor_tokens = _tokens(str(photo.get("page_anchor") or ""))
    scored: list[tuple[float, dict]] = []
    for page in index:
        page_numbers = {str(question.get("question_no", "")).strip() for question in page.get("questions", [])}
        union = numbers | page_numbers
        number_score = len(numbers & page_numbers) / len(union) if union else 0.0
        page_tokens = _tokens(str(page.get("anchor_text") or ""))
        anchor_score = len(anchor_tokens & page_tokens) / max(1, len(anchor_tokens | page_tokens))
        score = min(1.0, number_score * 0.78 + anchor_score * 0.22)
        scored.append((score, page))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return None, 0.0, []
    return scored[0][1], scored[0][0], [page for _, page in scored[:2]]


class RecognitionJob:
    def __init__(self, config: dict, report: Callable[[dict], None], cancel_event: threading.Event):
        self.config = config
        self.report = report
        self.cancel_event = cancel_event
        self.output = Path(config["output_dir"])
        self.pdf = Path(config["clean_pdf"])
        self.state_path = self.output / "recognition_job_state.json"
        self.result_path = self.output / "recognition_result.json"
        self.usage = {"api_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.budget = int(config["call_budget"])
        self._usage_lock = threading.Lock()
        self._reserved_calls = 0

    def _save(self, state: dict) -> None:
        _atomic_json(self.state_path, state)

    def _add_usage(self, reply) -> None:
        with self._usage_lock:
            self._reserved_calls = max(0, self._reserved_calls - 1)
            self.usage["api_calls"] += 1
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                self.usage[key] += int(reply.usage.get(key, 0) or 0)

    def _require_budget(self) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("任务已取消")
        with self._usage_lock:
            if self.usage["api_calls"] + self._reserved_calls >= self.budget:
                raise BudgetExceeded("已达到本任务 API 调用预算；请增加预算后继续")
            self._reserved_calls += 1

    def _release_reservation(self) -> None:
        with self._usage_lock:
            self._reserved_calls = max(0, self._reserved_calls - 1)

    def _call(self, request: Callable[..., Any], *args: Any) -> Any:
        self._require_budget()
        try:
            reply = request(*args)
        except Exception:
            self._release_reservation()
            raise
        self._add_usage(reply)
        return reply

    def _load_or_index(self, client: AiClient) -> list[dict]:
        pdf_hash = _sha256(self.pdf)
        provider = getattr(client, "provider", "custom")
        model = client.model
        with fitz.open(self.pdf) as document:
            count = document.page_count

        cached = load_pdf_index_cache(pdf_hash, page_count=count, provider=provider, model=model)
        if cached is not None:
            self.report({
                "phase": "indexing_pdf",
                "current_text": f"已复用本机 PDF 题目索引（{count} 页，0 次索引调用）",
                "progress": 8,
                "usage": self.usage,
                "pdf_index_cached": True,
                "pdf_index_calls": 0,
                "pdf_page_count": count,
            })
            return cached["pages"]

        indexed: list[dict] = []
        total_batches = max(1, (count + PAGE_BATCH_SIZE - 1) // PAGE_BATCH_SIZE)
        for start in range(0, count, PAGE_BATCH_SIZE):
            pages = [(page + 1, _render_page(self.pdf, page)) for page in range(start, min(start + PAGE_BATCH_SIZE, count))]
            reply = self._call(client.index_pdf_pages, pages)
            allowed = {number for number, _ in pages}
            for record in reply.data.get("pages", []):
                if record.get("page_number") not in allowed:
                    continue
                questions = []
                for question in record.get("questions", []):
                    box = _bbox(question.get("bbox"))
                    number = str(question.get("question_no") or "").strip()
                    if number and box:
                        questions.append({"question_no": number, "bbox": box})
                indexed.append({
                    "page_number": int(record["page_number"]),
                    "anchor_text": str(record.get("anchor_text") or ""),
                    "questions": questions,
                })
            done_pages = min(start + PAGE_BATCH_SIZE, count)
            done_batches = (done_pages + PAGE_BATCH_SIZE - 1) // PAGE_BATCH_SIZE
            self.report({
                "phase": "indexing_pdf",
                "current_text": f"正在建立 PDF 题目索引：{done_pages}/{count} 页（约 {done_batches}/{total_batches} 次调用）",
                "progress": 5 + int(18 * done_pages / count),
                "usage": self.usage,
                "pdf_index_cached": False,
                "pdf_index_calls": done_batches,
                "pdf_page_count": count,
            })

        # Keep only one record per page number, and pad missing pages so the
        # cache is reusable even when the model skips a sparse page.
        unique: dict[int, dict] = {}
        for item in indexed:
            unique[int(item["page_number"])] = item
        indexed = []
        for number in range(1, count + 1):
            indexed.append(
                unique.get(
                    number,
                    {"page_number": number, "anchor_text": "", "questions": []},
                )
            )
        if not any(item.get("questions") or item.get("anchor_text") for item in indexed):
            raise RuntimeError("识别服务未能识别干净 PDF 的题目索引")

        written = save_pdf_index_cache(
            pdf_hash,
            indexed,
            provider=str(provider),
            model=str(model),
            page_count=count,
        )
        cache_note = f"，已缓存到本机（{len(written)} 处）" if written else "，缓存写入失败（下次可能仍需全量索引）"
        self.report({
            "phase": "indexing_pdf",
            "current_text": f"PDF 题目索引完成：{count} 页{cache_note}",
            "progress": 22,
            "usage": self.usage,
            "pdf_index_cached": False,
            "pdf_index_calls": total_batches,
            "pdf_page_count": count,
        })
        return indexed

    def _recognize_photo(self, client: AiClient, student: str, photo_path: Path, index: list[dict]) -> dict:
        data = _read_photo(photo_path)
        reply = self._call(client.inspect_photo, data)
        record = reply.data
        page, confidence, candidates = _match_page(record, index)
        if (page is None or confidence < LOW_CONFIDENCE) and candidates:
            candidate_images = [(candidate["page_number"], _render_page(self.pdf, candidate["page_number"] - 1)) for candidate in candidates]
            verified = self._call(client.verify_page, data, candidate_images)
            selected = verified.data.get("pdf_page")
            if isinstance(selected, int):
                page = next((item for item in candidates if item["page_number"] == selected), None)
                confidence = max(0.0, min(1.0, float(verified.data.get("confidence", 0) or 0)))
        references = {str(question["question_no"]): question["bbox"] for question in (page or {}).get("questions", [])}
        visible: list[dict] = []
        for question in record.get("visible_questions", []):
            box = _bbox(question.get("photo_bbox"))
            number = str(question.get("question_no") or "").strip()
            status = question.get("status")
            if not number or not box or status not in {"wrong", "correct", "unknown"}:
                continue
            try:
                number_confidence = max(0.0, min(1.0, float(question.get("number_confidence", 0))))
                status_confidence = max(0.0, min(1.0, float(question.get("status_confidence", 0))))
            except (TypeError, ValueError):
                continue
            visible.append({"question_no": number, "photo_bbox": box, "reference_bbox": references.get(number), "status": status, "number_confidence": number_confidence, "status_confidence": status_confidence, "evidence": str(question.get("evidence") or "")})
        reliable = page is not None and confidence >= LOW_CONFIDENCE and bool(visible)
        return {"student_name": student, "photo_file": str(photo_path.resolve()), "matched_reference_file": str(self.pdf.resolve()), "pdf_page": page.get("page_number") if page else None, "page_match_confidence": confidence, "visible_questions": visible, "needs_manual_review": bool(record.get("needs_manual_review")) or not reliable, "review_reason": str(record.get("review_reason") or ("未能可靠匹配干净 PDF 页面" if not reliable else ""))}

    def _build_client(self) -> AiClient:
        if self.config.get("api_key") and self.config.get("base_url") and self.config.get("model"):
            return AiClient(
                str(self.config["api_key"]),
                str(self.config["model"]),
                str(self.config["base_url"]),
                provider=str(self.config.get("provider") or "custom"),
            )
        conn = ai_settings.get_connection()
        return AiClient(conn["api_key"], conn["model"], conn["base_url"], provider=conn["provider"])

    def run(self) -> dict:
        client = self._build_client()
        provider = getattr(client, "provider", str(self.config.get("provider") or "custom"))
        model = client.model
        students = self.config["students"]
        existing: dict[str, dict] = {}
        if self.result_path.is_file():
            try:
                prior = json.loads(self.result_path.read_text(encoding="utf-8"))
                existing = {str(item.get("photo_file")): item for item in prior.get("images", []) if isinstance(item, dict)}
            except (OSError, json.JSONDecodeError):
                existing = {}
        result = {
            "schema_version": SCHEMA_VERSION,
            "provider": provider,
            "model": model,
            "base_url": getattr(client, "base_url", ""),
            "prompt_version": PROMPT_VERSION,
            "clean_pdf": {"path": str(self.pdf.resolve()), "sha256": _sha256(self.pdf)},
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "images": list(existing.values()),
            "failures": [],
            "usage": self.usage,
        }
        try:
            index = self._load_or_index(client)
        except BudgetExceeded as exc:
            result["status"] = "budget_paused"
            result["paused_reason"] = str(exc)
            _atomic_json(self.result_path, result)
            self._save({"status": "budget_paused", "result": str(self.result_path), "usage": self.usage, "output_dir": str(self.output)})
            return result
        photos = [(item["student"], Path(photo)) for item in students for photo in item["photos"]]
        pending = [(ordinal, student, photo) for ordinal, (student, photo) in enumerate(photos, start=1) if str(photo.resolve()) not in existing]
        photo_order = {str(photo.resolve()): ordinal for ordinal, (_, photo) in enumerate(photos, start=1)}
        completed = len(existing)
        if pending:
            # Free-tier providers (智谱 flash etc.) rate-limit hard; process photos one-by-one.
            provider_name = str(getattr(client, "provider", "") or "")
            workers = 1 if provider_name in {"zhipu", "dashscope", "deepseek", "google", "openai", "xai"} else min(2, len(pending))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai-photo") as pool:
                source = iter(pending)
                futures: dict[Any, tuple[int, str, Path]] = {}

                def schedule_one() -> bool:
                    try:
                        ordinal, student, photo = next(source)
                    except StopIteration:
                        return False
                    futures[pool.submit(self._recognize_photo, client, student, photo, index)] = (ordinal, student, photo)
                    return True

                for _ in range(workers):
                    schedule_one()
                while futures:
                    done, _ = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        ordinal, student, photo = futures.pop(future)
                        key = str(photo.resolve())
                        completed += 1
                        try:
                            result["images"].append(future.result())
                        except (InterruptedError, BudgetExceeded) as exc:
                            result["status"] = "cancelled" if isinstance(exc, InterruptedError) else "budget_paused"
                            result["paused_reason"] = str(exc)
                        except Exception as exc:
                            result["failures"].append({"student": student, "photo_file": key, "reason": str(exc)})
                        result["usage"] = self.usage
                        result["images"].sort(key=lambda item: photo_order.get(str(item.get("photo_file")), len(photos)))
                        _atomic_json(self.result_path, result)
                        self._save({"status": result.get("status", "running"), "result": str(self.result_path), "completed_count": completed, "total_count": len(photos), "usage": self.usage, "output_dir": str(self.output)})
                        self.report({"phase": "recognizing_photos", "current_text": f"已处理 {student}：{photo.name}", "progress": 23 + int(68 * completed / max(1, len(photos))), "completed_count": completed, "total_count": len(photos), "usage": self.usage})
                        if result.get("status") not in {"cancelled", "budget_paused"}:
                            schedule_one()
        if result.get("status") in {"cancelled", "budget_paused"}:
            result["usage"] = self.usage
            _atomic_json(self.result_path, result)
            self._save({"status": result["status"], "result": str(self.result_path), "usage": self.usage, "output_dir": str(self.output)})
            return result
        self.report({"phase": "importing", "current_text": "正在生成教师复核工作台", "progress": 94, "usage": self.usage})
        imported = recognition_import.import_recognition_result(self.result_path, self.pdf, self.output)
        result["status"] = "partial" if result["failures"] or imported.get("issues") else "success"
        result["usage"] = self.usage
        _atomic_json(self.result_path, result)
        self._save({"status": result["status"], "result": str(self.result_path), "usage": self.usage, "output_dir": str(self.output)})
        return {**imported, "status": result["status"], "failed_photo_count": len(result["failures"]), "usage": self.usage, "recognition_result": str(self.result_path)}


# Backward-compatible alias
GrokRecognitionJob = RecognitionJob


def preflight(clean_pdf: str | Path, photo_root: str | Path, output_root: str | Path | None, selected_students: list[str] | None = None) -> dict:
    pdf = Path(clean_pdf).expanduser().resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise ValueError("干净练习册 PDF 不存在或不是 PDF")
    all_students = discover_students(photo_root)
    wanted = set(selected_students or [item["student"] for item in all_students])
    students = [item for item in all_students if item["student"] in wanted]
    if not students:
        raise ValueError("请至少选择一名学生")
    photo_count = sum(len(item["photos"]) for item in students)
    settings = ai_settings.summary()
    provider = settings.get("provider") or ai_settings.DEFAULT_PROVIDER
    model = settings.get("model") or ai_settings.DEFAULT_MODEL
    index_calls, cached, page_count = estimate_index_calls(pdf, provider=provider, model=model)
    base_calls = index_calls + photo_count
    budget = base_calls + max(1, int(np.ceil(photo_count * 0.2)))
    root = Path(output_root).expanduser().resolve() if output_root else Path(photo_root).expanduser().resolve().parent / "错题集输出"
    return {
        "config": {
            "clean_pdf": str(pdf),
            "photo_root": str(Path(photo_root).expanduser().resolve()),
            "output_root": str(root),
            "students": students,
            "provider": provider,
            "model": model,
            "base_url": settings.get("effective_base_url") or settings.get("base_url") or "",
            "base_calls": base_calls,
            "call_budget": budget,
            "pdf_index_calls": index_calls,
            "pdf_index_cached": cached,
            "pdf_page_count": page_count,
        },
        "students": [{"student": item["student"], "photo_count": len(item["photos"]), "selected": item["student"] in wanted} for item in all_students],
        "photo_count": photo_count,
        "pdf_page_count": page_count,
        "pdf_index_cached": cached,
        "pdf_index_calls": index_calls,
        "photo_calls": photo_count,
        "base_calls": base_calls,
        "call_budget": budget,
        "provider": provider,
        "model": model,
        "issues": [],
    }
