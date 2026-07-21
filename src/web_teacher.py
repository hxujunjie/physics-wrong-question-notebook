"""Local-only browser host for the multi-vendor teacher workflow."""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2

from . import ai_settings, config, recognition_import, recognition_pipeline, render_pdf, review_workspace, teacher_pipeline
from .ai_client import AiClient

APP_VERSION = "1.6.2"

# Windows browsers often abort keep-alive sockets while the handler is still writing.
_CLIENT_DISCONNECT_ERRORS = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionError,
    TimeoutError,
)


def _is_client_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, _CLIENT_DISCONNECT_ERRORS):
        return True
    if isinstance(exc, OSError):
        # WinError 10053/10054 = connection aborted/reset by peer.
        if getattr(exc, "winerror", None) in {10053, 10054, 10058}:
            return True
        if getattr(exc, "errno", None) in {32, 104, 54, 10053, 10054}:  # EPIPE / ECONNRESET family
            return True
    return False


def _error_message(exc: BaseException, *, limit: int = 1500) -> str:
    try:
        text = str(exc)
    except Exception:
        text = repr(exc)
    text = text.replace("\r", " ").replace("\n", " ").strip() or exc.__class__.__name__
    return text[:limit]


class WebTeacherServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, delivery_root: Path, port: int = 8765, web_root: Path | None = None):
        self.delivery_root = Path(delivery_root).resolve()
        self.web_root = Path(web_root).resolve() if web_root else (self.delivery_root / "web").resolve()
        self.runtime_path = self.delivery_root / "_runtime" / "web_server.json"
        self.token = secrets.token_urlsafe(32)
        self.job_lock = threading.Lock()
        self.job = {"status": "idle", "phase": "idle", "progress": 0, "current_text": "", "error": None, "result": None, "usage": {}, "completed_count": 0, "total_count": 0, "call_budget": 0}
        self.cancel_event = threading.Event()
        self.last_config: dict | None = None
        super().__init__(("127.0.0.1", port), WebTeacherHandler)

    def handle_error(self, request, client_address):
        """Do not treat browser disconnects as fatal server errors."""
        import sys

        exc = sys.exc_info()[1]
        if exc is not None and _is_client_disconnect(exc):
            return
        super().handle_error(request, client_address)

    def runtime_record(self):
        return {"pid": os.getpid(), "port": self.server_port, "token": self.token, "started_at": datetime.now().isoformat(timespec="seconds")}

    def write_runtime(self):
        self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_path.write_text(json.dumps(self.runtime_record(), ensure_ascii=False, indent=2), encoding="utf-8")

    def shutdown_cleanly(self):
        self.runtime_path.unlink(missing_ok=True)
        threading.Thread(target=self.shutdown, daemon=True).start()

    def start_recognition(self, data: dict):
        with self.job_lock:
            if self.job["status"] == "running":
                return False
            self.cancel_event = threading.Event()
            self.last_config = data
            self.job = {
                "status": "running",
                "phase": "preparing",
                "progress": 0,
                "current_text": "正在准备智能识别任务",
                "error": None,
                "result": None,
                "usage": {},
                "completed_count": 0,
                "total_count": sum(len(item["photos"]) for item in data["students"]),
                "call_budget": data["call_budget"],
            }

        def report(update: dict):
            with self.job_lock:
                if self.job["status"] == "running":
                    self.job.update(update)

        def worker():
            try:
                result = recognition_pipeline.RecognitionJob(data, report, self.cancel_event).run()
                with self.job_lock:
                    status = result.get("status", "success")
                    current_text = (
                        "识别完成，请确认不确定项后生成错题集"
                        if status in {"success", "partial"}
                        else ("调用预算已到达，可追加后继续" if status == "budget_paused" else "任务已取消")
                    )
                    self.job.update(
                        status=status,
                        phase="review_ready" if status in {"success", "partial"} else status,
                        progress=100 if status in {"success", "partial"} else self.job.get("progress", 0),
                        result=result,
                        current_text=current_text,
                    )
            except Exception as exc:
                with self.job_lock:
                    self.job.update(status="failed", phase="failed", error=str(exc), current_text="智能识别失败")

        threading.Thread(target=worker, daemon=True).start()
        return True

    # Backward-compatible alias
    start_grok_recognition = start_recognition


class WebTeacherHandler(BaseHTTPRequestHandler):
    server: WebTeacherServer
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def handle_one_request(self):
        """Same as base, but swallow client-abort noise during write-back."""
        try:
            super().handle_one_request()
        except Exception as exc:
            if _is_client_disconnect(exc):
                return
            raise

    def _json(self, value, status: int = 200) -> None:
        """Send JSON. Never raise ConnectionAbortedError to the request loop."""
        try:
            try:
                raw = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
            except Exception:
                raw = json.dumps(
                    {"error": "服务器响应无法序列化为 JSON", "status": status},
                    ensure_ascii=False,
                ).encode("utf-8")
                status = 500
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)
            try:
                self.wfile.flush()
            except Exception:
                pass
        except Exception as exc:
            # Browser closed the socket (common on Windows) or write failed after partial send.
            # Swallow it so ThreadingHTTPServer keeps serving other requests.
            if not _is_client_disconnect(exc):
                # Still do not re-raise: a broken client must not kill the teacher server process.
                return
            return

    def _error_json(self, status: int, message: object) -> None:
        text = _error_message(message) if isinstance(message, BaseException) else str(message or "未知错误")[:1500]
        self._json({"error": text}, status)

    def _body(self):
        length = self.headers.get("Content-Length")
        try:
            size = int(length) if length else 0
        except (TypeError, ValueError):
            raise ValueError("Content-Length 无效") from None
        if size < 0 or size > 32 * 1024 * 1024:
            raise ValueError("请求体过大或无效")
        raw = self.rfile.read(size) if size else b"{}"
        try:
            return json.loads(raw or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("JSON 请求无法解析") from exc

    def _guard(self):
        provided = self.headers.get("X-Review-Token", "") or ""
        expected = self.server.token or ""
        try:
            ok = secrets.compare_digest(provided, expected)
        except (TypeError, ValueError):
            ok = False
        if not ok:
            self._json({"error": "Forbidden"}, 403)
            return False
        return True

    @staticmethod
    def _inside(path: Path, root: Path) -> Path:
        resolved, root = path.resolve(), root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError("路径超出允许范围")
        return resolved

    def _manifest(self, output_dir, student):
        root = Path(output_dir).resolve()
        if not root.is_dir(): raise ValueError("输出目录不存在")
        if not student or Path(student).name != student or student in {".", ".."}: raise ValueError("学生路径无效")
        cache = self._inside(root / "_cache" / student, root)
        if not cache.is_dir(): raise ValueError("输出目录中没有该学生")
        return root, review_workspace.load_manifest(cache / "review_manifest.json")

    @staticmethod
    def _recognition_config(data: dict, create_output: bool = False) -> dict:
        preflight = recognition_pipeline.preflight(
            data.get("clean_pdf", ""),
            data.get("photo_root", ""),
            data.get("output_root") or None,
            data.get("selected_students") or None,
        )
        config = preflight["config"]
        budget = int(data.get("call_budget") or config["call_budget"])
        if budget < config["base_calls"]:
            raise ValueError("调用预算不能低于基础调用数")
        config["call_budget"] = budget
        # Attach live credentials so batch workers use the teacher-selected vendor.
        try:
            conn = ai_settings.get_connection()
            config.update(
                {
                    "provider": conn["provider"],
                    "model": conn["model"],
                    "base_url": conn["base_url"],
                    "api_key": conn["api_key"],
                }
            )
        except ValueError:
            pass
        if create_output:
            config["output_dir"] = str(recognition_pipeline.make_output_dir(config["output_root"], config["clean_pdf"]))
        return {"config": config, "preflight": preflight}

    def do_GET(self):
        url, query = urlparse(self.path), parse_qs(urlparse(self.path).query)
        try:
            if url.path == "/api/health":
                provider = ai_settings.summary()
                return self._json(
                    {
                        "status": "ok",
                        "app_version": APP_VERSION,
                        "pipeline_version": teacher_pipeline.PIPELINE_VERSION,
                        "port": self.server.server_port,
                        "job_state": self.server.job,
                        "provider": provider,
                        "grok": provider,  # backward-compatible field name for older UI
                    }
                )
            if url.path == "/api/job":
                return self._json(self.server.job)
            if url.path in {"/api/settings/provider", "/api/settings/grok"}:
                return self._json(ai_settings.summary())
            if url.path == "/api/settings/providers":
                return self._json({"providers": ai_settings.list_presets()})
            if url.path == "/api/students":
                return self._students(query.get("output_dir", [""])[0])
            if url.path == "/api/manifest":
                _, manifest = self._manifest(query.get("output_dir", [""])[0], query.get("student", [""])[0])
                return self._json(manifest)
            if url.path == "/api/photo":
                return self._photo(query)
            if url.path == "/api/pdf-page":
                return self._pdf_page(query)
            if url.path == "/api/config":
                return self._json({"job": self.server.job})
            return self._static(url.path)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            self._error_json(400, exc)
        except Exception as exc:
            if _is_client_disconnect(exc):
                return
            self._error_json(500, exc)

    def _students(self, output_dir):
        root = Path(output_dir).resolve()
        if not root.is_dir(): raise ValueError("输出目录不存在")
        cache = root / "_cache"
        rows = []
        if not cache.is_dir(): return self._json({"students": []})
        for path in sorted(cache.glob("*/review_manifest.json")):
            manifest = review_workspace.load_manifest(path); pages = manifest.get("photo_tasks", []); questions = [q for p in pages for q in p.get("page_review_questions", [])]
            rows.append({"student": manifest.get("student", path.parent.name), "photo_count": len(pages), "wrong_question_count": sum(1 for q in questions if q.get("decision") == "wrong" and q.get("content_complete")), "low_confidence_count": sum(1 for q in questions if q.get("requires_review") and q.get("decision") is None), "reviewed_page_count": sum(1 for p in pages if p.get("review_completed")), "total_review_page_count": len(pages), "status": "review_required" if any(not p.get("review_completed") for p in pages) else "reviewed", "failed_photo_count": 0, "review_available": bool(pages)})
        return self._json({"students": rows})

    def _photo(self, query):
        root, manifest = self._manifest(query.get("output_dir", [""])[0], query.get("student", [""])[0]); sha = query.get("photo_sha256", [""])[0]
        page = next((p for p in manifest.get("photo_tasks", []) if p.get("photo_sha256") == sha), None)
        if not page: raise ValueError("照片不在当前 manifest 中")
        photo = Path(page.get("rectified_photo") or page.get("photo_path") or "").resolve()
        if not photo.is_file(): raise FileNotFoundError("照片不存在")
        data = photo.read_bytes(); self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(photo.name)[0] or "application/octet-stream"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def _pdf_page(self, query):
        _, manifest = self._manifest(query.get("output_dir", [""])[0], query.get("student", [""])[0])
        try: page_index = int(query.get("page_index", [""])[0])
        except ValueError as exc: raise ValueError("PDF 页码必须是 0 基整数") from exc
        import fitz
        with fitz.open(manifest["clean_pdf_path"]) as document:
            if page_index < 0 or page_index >= document.page_count: raise ValueError("PDF 页码超出范围")
        image = render_pdf.render_page(manifest["clean_pdf_path"], page_index, dpi=160); ok, encoded = cv2.imencode(".png", image)
        if not ok: raise ValueError("PDF 页面渲染失败")
        data = encoded.tobytes(); self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        try:
            if not self._guard():
                return
            data, path = self._body(), urlparse(self.path).path
            if path == "/api/pick-path":
                return self._pick(data)
            if path in {"/api/settings/provider", "/api/settings/grok"}:
                if data.get("clear"):
                    return self._json(ai_settings.clear())
                auto_detect = bool(data.get("auto_detect"))
                # auto_detect: ignore form provider/model/url so stale grok-4.5 + api.x.ai cannot win.
                if auto_detect:
                    return self._json(
                        ai_settings.save(
                            str(data.get("api_key") or ""),
                            None,
                            provider=None,
                            base_url=None,
                            auto_detect=True,
                        )
                    )
                provider = data.get("provider")
                model = data.get("model")
                base_url = data.get("base_url")
                return self._json(
                    ai_settings.save(
                        str(data.get("api_key") or ""),
                        None if model in (None, "") else str(model),
                        provider=str(provider) if provider else None,
                        base_url=None if base_url in (None, "") else str(base_url),
                        auto_detect=False,
                    )
                )
            if path in {"/api/settings/provider/test", "/api/settings/grok/test"}:
                conn = ai_settings.get_connection()
                usage = AiClient(conn["api_key"], conn["model"], conn["base_url"], provider=conn["provider"]).test_connection()
                return self._json({"status": "ok", "usage": usage})
            if path == "/api/preflight":
                if not ai_settings.summary().get("configured"):
                    raise ValueError("请先保存识别服务 API Key")
                return self._json(self._recognition_config(data)["preflight"])
            if path == "/api/batch":
                if not ai_settings.summary().get("configured"):
                    raise ValueError("请先保存识别服务 API Key")
                config_data = self._recognition_config(data, create_output=True)["config"]
                if not self.server.start_recognition(config_data):
                    return self._json({"error": "任务已在运行"}, 409)
                return self._json({"status": "running", "output_dir": config_data["output_dir"]}, 202)
            if path == "/api/import-recognition":
                return self._import_recognition(data)
            if path == "/api/cancel":
                if self.server.job.get("status") != "running":
                    return self._json({"status": self.server.job.get("status")})
                self.server.cancel_event.set()
                return self._json({"status": "cancelling"})
            if path == "/api/continue-budget":
                if not self.server.last_config:
                    raise ValueError("没有可继续的识别任务")
                extra = int(data.get("extra_calls") or 0)
                if extra <= 0:
                    raise ValueError("追加调用数必须大于 0")
                config_data = {**self.server.last_config, "call_budget": int(self.server.last_config["call_budget"]) + extra}
                if not self.server.start_recognition(config_data):
                    return self._json({"error": "任务已在运行"}, 409)
                return self._json({"status": "running"}, 202)
            if path == "/api/retry-failures":
                if not self.server.last_config:
                    raise ValueError("没有可重试的识别任务")
                if not self.server.start_recognition(self.server.last_config):
                    return self._json({"error": "任务已在运行"}, 409)
                return self._json({"status": "running"}, 202)
            if path == "/api/decision":
                root, _ = self._manifest(data["output_dir"], data["student"]); question = teacher_pipeline.update_review_decision(root, data["student"], data["evidence_id"], data["decision"]); manifest = review_workspace.load_manifest(root / "_cache" / data["student"] / "review_manifest.json"); return self._json({"question": question, "pdf_dirty": manifest.get("pdf_dirty", False)})
            if path == "/api/question":
                root, manifest = self._manifest(data["output_dir"], data["student"]); manifest_path = root / "_cache" / data["student"] / "review_manifest.json"; question = review_workspace.update_question(manifest, data["evidence_id"], qnum=data.get("qnum")); review_workspace.save_manifest(manifest_path, manifest); return self._json({"question": question})
            if path == "/api/photo-rotation":
                root, manifest = self._manifest(data["output_dir"], data["student"])
                manifest_path = root / "_cache" / data["student"] / "review_manifest.json"
                page = review_workspace.set_photo_display_rotation(
                    manifest,
                    str(data.get("photo_sha256") or ""),
                    data.get("degrees", 0),
                )
                review_workspace.save_manifest(manifest_path, manifest)
                return self._json({"page": page})
            if path == "/api/photo-pdf-page":
                root, manifest = self._manifest(data["output_dir"], data["student"])
                manifest_path = root / "_cache" / data["student"] / "review_manifest.json"
                # Accept 1-based page number from the teacher header editor.
                raw_page = data.get("pdf_page", data.get("page_number", data.get("page_index")))
                try:
                    page_1based = int(raw_page)
                except (TypeError, ValueError) as exc:
                    raise ValueError("PDF 页码必须是正整数") from exc
                if page_1based < 1:
                    raise ValueError("PDF 页码必须从 1 开始")
                import fitz
                with fitz.open(manifest["clean_pdf_path"]) as document:
                    if page_1based > document.page_count:
                        raise ValueError(f"PDF 只有 {document.page_count} 页")
                result = review_workspace.sync_photo_task_pdf_page(
                    manifest,
                    str(data.get("photo_sha256") or ""),
                    page_1based - 1,
                    actor="teacher_header_page",
                )
                review_workspace.save_manifest(manifest_path, manifest)
                return self._json(result)
            if path == "/api/manual-crop": return self._manual_crop(data)
            if path == "/api/complete-page":
                root, manifest = self._manifest(data["output_dir"], data["student"]); page = next((p for p in manifest.get("photo_tasks", []) if p.get("photo_sha256") == data["photo_sha256"]), None)
                if page is None: raise ValueError("页面不存在")
                allowed, remaining = review_workspace.page_can_complete(page)
                if not allowed and not data.get("allow_unresolved"): return self._json({"error": "当前页仍有待确认题目", "remaining": remaining}, 409)
                return self._json({"page": teacher_pipeline.complete_review_page(root, data["student"], data["photo_sha256"], bool(data.get("allow_unresolved"))), "rebuild": "deferred"})
            if path == "/api/finalize":
                if self.server.job["status"] == "running": return self._json({"error": "识别仍在进行，暂不能导出"}, 409)
                output = Path(data.get("output_dir", "")).resolve()
                if not output.is_dir() or not (output / "_cache").is_dir(): raise ValueError("输出目录不是当前任务目录")
                return self._json(teacher_pipeline.finalize_delivery(output, bool(data.get("allow_incomplete", False))))
            if path == "/api/open-output":
                output = Path(data["output_dir"]).resolve()
                if not output.is_dir(): raise ValueError("输出目录不存在")
                os.startfile(str(output)); return self._json({"status": "opened"})
            if path == "/api/shutdown": self._json({"status": "stopping"}); self.server.shutdown_cleanly(); return
            self._json({"error": "Unknown API"}, 404)
        except ValueError as exc:
            self._error_json(400, exc)
        except KeyError as exc:
            self._error_json(400, f"缺少字段: {exc}")
        except Exception as exc:
            if _is_client_disconnect(exc):
                return
            self._error_json(500, exc)

    def _manual_crop(self, data):
        root, manifest = self._manifest(data["output_dir"], data["student"])
        raw = data.get("manual_segments")
        if not isinstance(raw, list) or not raw:
            raise ValueError("至少需要一个 PDF 框选片段")
        import fitz
        with fitz.open(manifest["clean_pdf_path"]) as document:
            page_count = document.page_count
        segments = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict) or not isinstance(item.get("page_index"), int):
                raise ValueError(f"manual_segments[{index}] 页码无效")
            bbox = item.get("bbox_norm")
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise ValueError(f"manual_segments[{index}] bbox 无效")
            values = [float(value) for value in bbox]
            if (
                item["page_index"] < 0
                or item["page_index"] >= page_count
                or any(value < 0 or value > 1 for value in values)
                or values[0] >= values[2]
                or values[1] >= values[3]
            ):
                raise ValueError(f"manual_segments[{index}] 范围或顺序无效")
            segments.append({
                "page_index": item["page_index"],
                "bbox_norm": values,
                "is_continuation": bool(item.get("is_continuation", index > 0)),
            })
        manifest_path = root / "_cache" / data["student"] / "review_manifest.json"
        result = review_workspace.apply_manual_crop(
            manifest,
            str(data.get("evidence_id") or ""),
            segments,
            sync_page_siblings=bool(data.get("sync_page_siblings", True)),
        )
        review_workspace.save_manifest(manifest_path, manifest)
        return self._json(result)

    def _import_recognition(self, data):
        if self.server.job["status"] == "running":
            return self._json({"error": "识别任务进行中，暂不能导入"}, 409)
        json_path = Path(str(data.get("recognition_json") or "")).expanduser()
        clean_pdf = Path(str(data.get("clean_pdf") or "")).expanduser()
        photo_root_raw = str(data.get("photo_root") or "").strip()
        if not json_path.is_file():
            raise ValueError("请选择 recognition_result.json")
        if not clean_pdf.is_file() or clean_pdf.suffix.lower() != ".pdf":
            raise ValueError("请选择干净练习册 PDF")
        if not photo_root_raw:
            raise ValueError("请选择学生照片总目录（与在线识别相同）")
        photo_root = Path(photo_root_raw).expanduser()
        if not photo_root.is_dir():
            raise ValueError("请选择学生照片总目录（与在线识别相同）")
        # Default next to photo pack (same mental model as online path hints).
        output_root = data.get("output_root") or str(photo_root.parent / "错题集输出")
        output_dir = recognition_pipeline.make_output_dir(output_root, clean_pdf)
        summary = recognition_import.import_recognition_result(
            json_path, clean_pdf, output_dir, photo_root=photo_root
        )
        with self.server.job_lock:
            self.server.job = {
                "status": summary.get("status", "success"),
                "phase": "review_ready",
                "progress": 100,
                "current_text": "JSON 导入完成，等待教师复核",
                "error": None,
                "result": summary,
                "usage": {},
                "completed_count": summary.get("photo_count", 0),
                "total_count": summary.get("photo_count", 0),
                "call_budget": 0,
            }
        return self._json({"status": summary.get("status", "success"), "output_dir": str(output_dir), "summary": summary})

    def _pick(self, data):
        import tkinter as tk
        from tkinter import filedialog

        kind = data.get("kind")
        if kind not in {"pdf", "directory", "json"}:
            raise ValueError("kind 必须是 pdf、directory 或 json")
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        try:
            if kind == "pdf":
                value = filedialog.askopenfilename(
                    parent=root, title="选择干净练习册 PDF", filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")]
                )
            elif kind == "json":
                value = filedialog.askopenfilename(
                    parent=root,
                    title="选择 recognition_result.json",
                    filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
                )
            else:
                title = "选择学生照片总目录" if data.get("purpose") == "photo_root" else "选择输出根目录"
                value = filedialog.askdirectory(parent=root, title=title)
        finally:
            root.destroy()
        return self._json({"path": value or ""})

    def _static(self, request_path):
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/"); path = self._inside(self.server.web_root / relative, self.server.web_root)
        if not path.is_file(): self.send_error(404); return
        data = path.read_bytes()
        if path.name == "index.html": data = data.replace(b"__REVIEW_TOKEN__", self.server.token.encode())
        self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


def make_server(delivery_root: str | Path, port: int = 8765, web_root: str | Path | None = None) -> WebTeacherServer:
    last = None
    for candidate in range(port, 8786):
        try: return WebTeacherServer(Path(delivery_root), candidate, Path(web_root) if web_root else None)
        except OSError as exc: last = exc
    raise RuntimeError("8765-8785 没有可用端口") from last
