"""OpenAI-compatible vision client for multi-vendor teacher recognition."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI


class AiError(RuntimeError):
    pass


class RateLimitError(AiError):
    """Provider rejected the request for too many calls in a short window."""


# Free-tier domestic APIs (especially 智谱 flash) often allow only a few RPM.
# Serialize requests process-wide so PDF indexing + photo threads cannot stampede.
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_MIN_INTERVAL_SECONDS = {
    "zhipu": 2.5,
    "dashscope": 1.2,
    "volcengine": 1.0,
    "deepseek": 1.0,
    "openai": 0.6,
    "google": 0.8,
    "xai": 1.0,
    "custom": 0.8,
}


@dataclass
class AiReply:
    data: dict[str, Any]
    usage: dict[str, int]


def _image_url(data: bytes, mime: str = "image/jpeg") -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}}


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return text


def _extract_message_text(message: Any) -> str:
    """Pull visible model text from OpenAI/Zhipu-style message objects."""
    if message is None:
        return ""
    raw = getattr(message, "content", None)
    if raw is None and isinstance(message, dict):
        raw = message.get("content")
    if isinstance(raw, list):
        parts: list[str] = []
        for part in raw:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(str(getattr(part, "text", None) or part))
        text = "".join(parts).strip()
    else:
        text = str(raw or "").strip()
    if text:
        return text
    # Some vision/thinking models put usable output only in reasoning_content.
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning is None and isinstance(message, dict):
        reasoning = message.get("reasoning_content")
    return str(reasoning or "").strip()


def _first_json_object(text: str) -> dict | None:
    """Parse the first complete JSON object, ignoring trailing extra data/text.

    Free VL models often return: ``{...valid json...}\\n说明：...`` or two objects.
    ``json.loads`` then fails with ``Extra data``; ``raw_decode`` recovers the first object.
    """
    if not text:
        return None
    decoder = json.JSONDecoder()
    # Prefer first object-looking segment; skip leading prose.
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _parse_json_object(raw: str) -> dict:
    text = _strip_code_fence(raw)
    if not text:
        raise AiError("识别服务没有返回内容")
    parsed = _first_json_object(text)
    if parsed is None:
        # Last resort: greedy brace match (may still fail on truncated JSON).
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            parsed = _first_json_object(match.group(0))
    if parsed is None:
        preview = re.sub(r"\s+", " ", text)[:180]
        raise AiError(f"识别服务返回的 JSON 无法解析（预览：{preview}）")
    return parsed


def _safe_error_text(exc: BaseException, *, limit: int = 400) -> str:
    """Never let secondary encoding issues hide the original failure."""
    try:
        text = str(exc)
    except Exception:
        text = repr(exc)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text[:limit] if len(text) > limit else text


class AiClient:
    """Sends in-memory image payloads only; never uploads source files to vendor file stores."""

    def __init__(self, api_key: str, model: str, base_url: str, *, provider: str = "custom", timeout: float = 180.0):
        if not api_key or not str(api_key).strip():
            raise ValueError("API Key 不能为空")
        if not model or not str(model).strip():
            raise ValueError("模型名不能为空")
        if not base_url or not str(base_url).strip():
            raise ValueError("接口地址不能为空")
        self.provider = (provider or "custom").strip()
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        # Explicit UTF-8 HTTP client: avoid locale/header mishaps on Chinese Windows.
        http_client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=30.0),
            headers={"Accept-Charset": "utf-8"},
        )
        self.client = OpenAI(
            api_key=api_key.strip(),
            base_url=self.base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
            default_headers={"Accept-Charset": "utf-8"},
        )

    def _provider_request_extras(self) -> dict[str, Any]:
        """Vendor-specific body fields that improve structured JSON reliability."""
        if self.provider == "zhipu":
            # glm-4.6v-flash 等默认开启 thinking 时，content 可能为空或夹杂思考文本，导致 JSON 解析失败。
            return {"thinking": {"type": "disabled"}}
        return {}

    def _throttle(self) -> None:
        """Space out calls so free-tier RPM limits are less likely to trip."""
        global _LAST_REQUEST_AT
        gap = float(_MIN_INTERVAL_SECONDS.get(self.provider, 1.0))
        with _REQUEST_LOCK:
            now = time.monotonic()
            wait_for = _LAST_REQUEST_AT + gap - now
            if wait_for > 0:
                time.sleep(wait_for)
            _LAST_REQUEST_AT = time.monotonic()

    def _create_completion(self, *, messages: list[dict], use_json_object: bool) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        extras = self._provider_request_extras()
        if extras:
            kwargs["extra_body"] = extras
        if use_json_object:
            kwargs["response_format"] = {"type": "json_object"}
        self._throttle()
        return self.client.chat.completions.create(**kwargs)

    def _request(self, prompt: str, images: Iterable[tuple[bytes, str]], schema_name: str, schema: dict) -> AiReply:
        # Schema is English keys; keep ensure_ascii=True so the instruction payload stays
        # maximally compatible with picky gateways, while Chinese prompt remains UTF-8 in body.
        instruction = (
            f"{prompt}\n\n"
            f"请严格返回符合 JSON Schema（name={schema_name}）的单个 JSON 对象，不要使用 Markdown 代码块，不要输出解释：\n"
            f"{json.dumps(schema, ensure_ascii=True)}"
        )
        content: list[dict] = [{"type": "text", "text": instruction}]
        content.extend(_image_url(image, mime) for image, mime in images)
        messages = [{"role": "user", "content": content}]
        last: Exception | None = None
        # Free models: longer 429 backoff. Attempts: immediate + 3 waits.
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                try:
                    response = self._create_completion(messages=messages, use_json_object=True)
                except APIStatusError as exc:
                    # Some domestic gateways reject response_format; retry without it.
                    if getattr(exc, "status_code", 0) in {400, 404, 422}:
                        response = self._create_completion(messages=messages, use_json_object=False)
                    else:
                        raise
                message = response.choices[0].message if response.choices else None
                raw = _extract_message_text(message)
                parsed = _parse_json_object(raw)
                usage_obj = getattr(response, "usage", None)
                usage = {
                    "input_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or getattr(usage_obj, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(usage_obj, "completion_tokens", 0) or getattr(usage_obj, "output_tokens", 0) or 0),
                    "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0),
                }
                if not usage["total_tokens"]:
                    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
                return AiReply(parsed, usage)
            except APIStatusError as exc:
                detail = _safe_error_text(exc)
                body = ""
                try:
                    body = _safe_error_text(getattr(exc, "body", "") or getattr(exc, "message", "") or "")
                except Exception:
                    body = ""
                status = getattr(exc, "status_code", 0) or 0
                combined = f"{body} {detail}".lower()
                is_rate = status == 429 or "1302" in combined or "rate" in combined or "速率" in combined or "限流" in combined
                if status not in {429, 500, 502, 503, 504} and not is_rate:
                    raise AiError(f"识别服务请求失败：HTTP {status} {body or detail}".strip()) from exc
                last = exc
                if is_rate and attempt >= max_attempts - 1:
                    raise RateLimitError(
                        "识别服务触发速率限制（请求太频繁）。"
                        "免费模型每分钟可调用次数很少，请等待 1～2 分钟后点「开始智能识别」继续；"
                        "已完成的照片不会重复识别。"
                    ) from exc
                if is_rate:
                    # 8s, 20s, 40s, 60s — free tier recovery windows are often tens of seconds.
                    time.sleep(min(60.0, 8.0 * (2**attempt)))
                    continue
            except (APITimeoutError, APIConnectionError) as exc:
                last = exc
            except AiError:
                raise
            except UnicodeError as exc:
                # Was previously swallowed as "JSON 无法解析" because UnicodeEncodeError ⊂ ValueError.
                raise AiError(
                    "识别服务通信出现编码错误。请确认 API Key/模型名只含英文数字，"
                    f"并重试。详情：{_safe_error_text(exc)}"
                ) from exc
            except json.JSONDecodeError as exc:
                raise AiError(f"识别服务返回 JSON 无法解析：{_safe_error_text(exc)}") from exc
            except ValueError as exc:
                raise AiError(f"识别服务返回数据异常：{_safe_error_text(exc)}") from exc
            if attempt < max_attempts - 1:
                time.sleep(min(20.0, 2.0 ** attempt))
        raise AiError(f"识别服务暂时不可用：{_safe_error_text(last) if last else 'unknown'}") from last

    def test_connection(self) -> dict:
        reply = self._request(
            '只返回 {"ok": true}，用于验证教师端识别服务连接。',
            [],
            "connection_test",
            {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
        )
        if not reply.data.get("ok"):
            raise AiError("连接测试没有返回成功状态")
        return reply.usage

    def index_pdf_pages(self, pages: list[tuple[int, bytes]]) -> AiReply:
        numbers = [number for number, _ in pages]
        question = {
            "type": "object",
            "properties": {
                "question_no": {"type": "string"},
                "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
            },
            "required": ["question_no", "bbox"],
            "additionalProperties": False,
        }
        page = {
            "type": "object",
            "properties": {
                "page_number": {"type": "integer"},
                "anchor_text": {"type": "string"},
                "questions": {"type": "array", "items": question},
            },
            "required": ["page_number", "anchor_text", "questions"],
            "additionalProperties": False,
        }
        schema = {"type": "object", "properties": {"pages": {"type": "array", "items": page}}, "required": ["pages"], "additionalProperties": False}
        prompt = (
            f"这些图片依次是干净物理练习册 PDF 的第 {numbers} 页。"
            "识别每页可见题号和每道题完整区域的 bbox [left,top,right,bottom]。"
            "bbox 必须是 0 到 1 之间的归一化比例（相对整页宽高），不要用像素整数。"
            "只输出一个 JSON 对象，不要附加说明、第二个 JSON 或 Markdown。"
            "并摘录可用于页面匹配的短锚点文本。不要猜测不存在的题号。"
        )
        return self._request(prompt, [(data, "image/jpeg") for _, data in pages], "pdf_page_index", schema)

    def inspect_photo(self, photo: bytes) -> AiReply:
        question = {
            "type": "object",
            "properties": {
                "question_no": {"type": "string"},
                "photo_bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                "status": {"type": "string", "enum": ["wrong", "correct", "unknown"]},
                "number_confidence": {"type": "number"},
                "status_confidence": {"type": "number"},
                "evidence": {"type": "string"},
            },
            "required": ["question_no", "photo_bbox", "status", "number_confidence", "status_confidence", "evidence"],
            "additionalProperties": False,
        }
        schema = {
            "type": "object",
            "properties": {
                "page_anchor": {"type": "string"},
                "needs_manual_review": {"type": "boolean"},
                "review_reason": {"type": "string"},
                "visible_questions": {"type": "array", "items": question},
            },
            "required": ["page_anchor", "needs_manual_review", "review_reason", "visible_questions"],
            "additionalProperties": False,
        }
        prompt = (
            "这是已经被老师批改的学生物理作业照片。"
            "仅根据明确的老师红勾、红叉、圈改和批注判断正误；痕迹不明确必须标记 unknown，不要自行解题推断。"
            "提取每道可见题目的题号和照片 bbox [left,top,right,bottom]（必须为 0~1 归一化比例，不要像素）。"
            "只输出一个 JSON 对象，不要附加说明。"
            "并给出页面锚点文本。"
        )
        return self._request(prompt, [(photo, "image/jpeg")], "marked_homework", schema)

    def verify_page(self, photo: bytes, candidates: list[tuple[int, bytes]]) -> AiReply:
        schema = {
            "type": "object",
            "properties": {"pdf_page": {"type": ["integer", "null"]}, "confidence": {"type": "number"}},
            "required": ["pdf_page", "confidence"],
            "additionalProperties": False,
        }
        labels = [number for number, _ in candidates]
        prompt = (
            f"第一张图是学生作业照片，后续图是干净 PDF 候选页 {labels}。"
            "判断照片对应哪一页；无法可靠判断时返回 null。"
            "只输出一个 JSON 对象，不要附加说明。"
        )
        return self._request(
            prompt,
            [(photo, "image/jpeg")] + [(page, "image/jpeg") for _, page in candidates],
            "page_verification",
            schema,
        )


# Backward-compatible aliases used by older tests/imports.
GrokError = AiError
GrokReply = AiReply
GrokClient = AiClient
