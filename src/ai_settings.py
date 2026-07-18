"""Windows-local multi-vendor AI settings with DPAPI-protected API keys."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from .ai_client import AiClient

def _models(*items: tuple[str, str, bool]) -> list[dict]:
    """Build model catalog entries: (id, label, is_free_or_low_cost)."""
    return [{"id": mid, "label": label, "free": free} for mid, label, free in items]


# Multi-vendor presets. models[] drives the teacher UI dropdown; default_model is pre-selected.
PROVIDER_PRESETS: dict[str, dict] = {
    "zhipu": {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4.6v-flash",
        "model_hint": "推荐免费视觉模型；批改识别需要多模态能力",
        "models": _models(
            ("glm-4.6v-flash", "glm-4.6v-flash（免费·推荐）", True),
            ("glm-4v-flash", "glm-4v-flash（免费）", True),
            ("glm-4.1v-thinking-flash", "glm-4.1v-thinking-flash（免费）", True),
            ("glm-4.6v", "glm-4.6v（付费·更强）", False),
            ("glm-4v", "glm-4v（付费）", False),
        ),
    },
    "dashscope": {
        "id": "dashscope",
        "name": "通义千问（阿里云）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen2.5-vl-3b-instruct",
        "model_hint": "默认低价开源视觉；旗舰 qwen-vl-plus/max 按量计费",
        "models": _models(
            ("qwen2.5-vl-3b-instruct", "qwen2.5-vl-3b-instruct（低价·推荐）", True),
            ("qwen2.5-vl-7b-instruct", "qwen2.5-vl-7b-instruct（低价）", True),
            ("qwen2.5-vl-32b-instruct", "qwen2.5-vl-32b-instruct", False),
            ("qwen-vl-plus", "qwen-vl-plus（商用）", False),
            ("qwen-vl-max", "qwen-vl-max（商用·最强）", False),
        ),
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI / GPT",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "model_hint": "需官方或兼容 sk- 密钥；推荐 gpt-4o-mini 控制成本",
        "models": _models(
            ("gpt-4o-mini", "gpt-4o-mini（低价视觉·推荐）", True),
            ("gpt-4o", "gpt-4o（付费·强）", False),
            ("gpt-4.1-mini", "gpt-4.1-mini", True),
            ("gpt-4.1", "gpt-4.1（付费）", False),
            ("o4-mini", "o4-mini（若账号可用）", False),
        ),
    },
    "google": {
        "id": "google",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        # 2026-07: gemini-2.5/2.0 对不少新 AI Studio 用户返回 404（no longer available to new users）。
        "default_model": "gemini-3.5-flash",
        "model_hint": "使用 Google AI Studio API Key；新用户请优先选 3.x。若下拉没有可选手填",
        "allow_custom_model": True,
        "models": _models(
            ("gemini-3.5-flash", "gemini-3.5-flash（推荐·新用户可用）", True),
            ("gemini-3.1-flash-lite", "gemini-3.1-flash-lite（更轻）", True),
            ("gemini-3-flash-preview", "gemini-3-flash-preview", True),
            ("gemini-flash-latest", "gemini-flash-latest（滚动最新）", True),
            ("gemini-pro-latest", "gemini-pro-latest（滚动更强）", False),
            # 保留旧名便于老账号；新用户调用可能 404。
            ("gemini-2.5-flash", "gemini-2.5-flash（旧·新用户常不可用）", False),
            ("gemini-2.5-pro", "gemini-2.5-pro（旧·新用户常不可用）", False),
            ("gemini-2.0-flash", "gemini-2.0-flash（旧·新用户常不可用）", False),
        ),
    },
    "xai": {
        "id": "xai",
        "name": "xAI Grok",
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-2-vision-1212",
        "model_hint": "密钥通常以 xai- 开头；请选带 vision 的模型",
        "models": _models(
            ("grok-2-vision-1212", "grok-2-vision-1212（视觉·推荐）", False),
            ("grok-2-vision", "grok-2-vision", False),
            ("grok-4", "grok-4（若账号可用）", False),
            ("grok-3", "grok-3", False),
        ),
    },
    "volcengine": {
        "id": "volcengine",
        "name": "豆包 / 火山方舟",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "",
        "model_hint": "请填写方舟控制台的「接入点模型 ID」",
        "models": [],
        "allow_custom_model": True,
    },
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "model_hint": "官方文本为主；批改识图请优先智谱/通义/Gemini/GPT 视觉模型",
        "models": _models(
            ("deepseek-chat", "deepseek-chat", True),
            ("deepseek-reasoner", "deepseek-reasoner", False),
        ),
    },
    "custom": {
        "id": "custom",
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "default_model": "",
        "model_hint": "中转站/私有化：自行填 Base URL 与模型名",
        "models": [],
        "allow_custom_model": True,
        "requires_base_url": True,
    },
}

# 识别不准时默认智谱免费视觉，避免误落到付费通义旗舰。
DEFAULT_PROVIDER = "zhipu"
_ENTROPY = b"physics-wrong-book:ai-api-key:v1"
# Keep old entropy so existing grok keys can still be decrypted during migration.
_LEGACY_ENTROPY = b"physics-wrong-book:grok-api-key:v1"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def settings_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("USERPROFILE") or ".")
    return root / "PhysicsWrongBook" / "settings.json"


def list_presets() -> list[dict]:
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "base_url": item["base_url"],
            "default_model": item["default_model"],
            "model_hint": item["model_hint"],
            "models": list(item.get("models") or []),
            "allow_custom_model": bool(item.get("allow_custom_model") or item["id"] in {"custom", "volcengine"}),
            "requires_base_url": bool(item.get("requires_base_url") or item["id"] == "custom"),
        }
        for item in PROVIDER_PRESETS.values()
    ]


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(value: str, *, entropy: bytes = _ENTROPY, description: str = "PhysicsWrongBook AI API Key") -> str:
    if os.name != "nt":
        raise RuntimeError("API Key 只能在 Windows 教师端保存")
    plain, _plain_buffer = _blob(value.encode("utf-8"))
    entropy_blob, _entropy_buffer = _blob(entropy)
    result = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(plain), description, ctypes.byref(entropy_blob), None, None, 0, ctypes.byref(result)
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return base64.b64encode(ctypes.string_at(result.pbData, result.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def _unprotect(value: str, *, entropy: bytes = _ENTROPY) -> str:
    if os.name != "nt":
        raise RuntimeError("API Key 只能在 Windows 教师端读取")
    encrypted, _encrypted_buffer = _blob(base64.b64decode(value.encode("ascii")))
    entropy_blob, _entropy_buffer = _blob(entropy)
    result = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(encrypted), None, ctypes.byref(entropy_blob), None, None, 0, ctypes.byref(result)
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def _unprotect_any(value: str) -> str:
    try:
        return _unprotect(value, entropy=_ENTROPY)
    except Exception:
        return _unprotect(value, entropy=_LEGACY_ENTROPY)


def _read(path: Path | None = None) -> dict:
    target = path or settings_path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _normalize(data: dict) -> dict:
    """Normalize current or legacy settings into the multi-vendor shape."""
    if data.get("grok_api_key") and not data.get("api_key"):
        # Legacy single-vendor Grok settings → dedicated xAI provider.
        return {
            "provider": "xai",
            "api_key": data.get("grok_api_key"),
            "model": data.get("model") or "grok-2-vision-1212",
            "base_url": None,
        }
    # Old installs stored xAI under custom + api.x.ai.
    provider_raw = str(data.get("provider") or "").strip()
    base_raw = str(data.get("base_url") or "").strip().lower()
    if provider_raw == "custom" and "api.x.ai" in base_raw:
        return {
            "provider": "xai",
            "api_key": data.get("api_key") or data.get("grok_api_key"),
            "model": data.get("model") or "grok-2-vision-1212",
            "base_url": None,
        }
    provider = str(data.get("provider") or DEFAULT_PROVIDER).strip()
    if provider not in PROVIDER_PRESETS:
        provider = DEFAULT_PROVIDER
    preset = PROVIDER_PRESETS[provider]
    model = str(data.get("model") or preset["default_model"] or "").strip()
    base_url = data.get("base_url")
    if base_url is not None:
        base_url = str(base_url).strip() or None
    return {
        "provider": provider,
        "api_key": data.get("api_key") or data.get("grok_api_key"),
        "model": model,
        "base_url": base_url,
    }


def resolve_base_url(provider: str, base_url: str | None = None) -> str:
    preset = PROVIDER_PRESETS.get(provider) or PROVIDER_PRESETS["custom"]
    value = (base_url or "").strip() or str(preset.get("base_url") or "").strip()
    if not value:
        raise ValueError("请填写接口地址（Base URL）")
    return value.rstrip("/")


LEGACY_GROK_MODELS = {
    "grok-4.5",
    "grok-4",
    "grok-3",
    "grok-2",
    "grok-2-vision-1212",
    "grok-vision-beta",
}
LEGACY_XAI_BASE = "https://api.x.ai/v1"


def _is_legacy_grok_settings(data: dict) -> bool:
    """True when stored settings still look like the old single-vendor Grok install."""
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip().lower()
    base = str(data.get("base_url") or "").strip().rstrip("/").lower()
    if data.get("grok_api_key") and not data.get("api_key"):
        return True
    # Intentional xAI provider is not "legacy residue".
    if provider == "xai":
        return False
    if "api.x.ai" in base and provider in {"", "custom"}:
        return True
    if model in LEGACY_GROK_MODELS or model.startswith("grok-"):
        if provider in {"", "custom"} or base == "" or "api.x.ai" in base:
            return True
    return False


def _looks_like_zhipu_key(key: str) -> bool:
    """智谱控制台密钥常见形态：id.secret（中间一个点），两侧均为较长 token。"""
    key = (key or "").strip()
    if not key or key.lower().startswith(("sk-", "xai-", "ark-")):
        return False
    if key.count(".") != 1:
        # 少数导出可能无点，仅一长串字母数字
        body = key.replace("-", "").replace("_", "")
        return len(key) >= 32 and body.isalnum()
    left, right = key.split(".", 1)
    if len(left) < 8 or len(right) < 8:
        return False
    # id / secret 一般为 URL-safe 字符，不能含空格
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    return all(ch in allowed for ch in left) and all(ch in allowed for ch in right)


def _provider_guess(provider_id: str, *, guess_label: str | None = None) -> dict:
    preset = PROVIDER_PRESETS[provider_id]
    return {
        "provider": provider_id,
        "base_url": preset["base_url"],
        "model": preset["default_model"],
        "guess_label": guess_label or preset["name"],
    }


def infer_from_api_key(api_key: str) -> dict:
    """Best-effort vendor guess from common key shapes. Teachers can still override.

    Important: 智谱密钥多为 ``id.secret``（含点号）。旧逻辑用 isalnum() 会把点号判失败，
    再落到通义 sk- 默认，导致“明明是智谱却识别成通义千问”。
    """
    key = (api_key or "").strip()
    low = key.lower()
    if low.startswith("xai-"):
        return _provider_guess("xai", guess_label="xAI Grok")
    if low.startswith("ark-"):
        return _provider_guess("volcengine", guess_label="豆包 / 火山方舟")
    # Google AI Studio keys commonly start with AIza.
    if key.startswith("AIza") or low.startswith("aiza"):
        return _provider_guess("google", guess_label="Google Gemini")
    # 智谱必须在 sk- 之前判断：密钥里通常有点，绝不是通义/OpenAI sk- 形态。
    if _looks_like_zhipu_key(key):
        return _provider_guess("zhipu", guess_label="智谱 GLM（免费视觉 glm-4.6v-flash）")
    # OpenAI project keys; plain sk- is ambiguous (also 通义/兼容中转).
    if low.startswith("sk-proj-") or low.startswith("sk-or-"):
        return _provider_guess("openai", guess_label="OpenAI / GPT")
    if low.startswith("sk-"):
        # Domestic product default: 通义. Teachers can switch to OpenAI in the dropdown.
        return _provider_guess("dashscope", guess_label="通义千问（sk- 密钥默认；可改选 OpenAI/GPT）")
    # 无法判断时优先智谱免费视觉，避免误落到付费旗舰。
    return _provider_guess(DEFAULT_PROVIDER, guess_label=f"{PROVIDER_PRESETS[DEFAULT_PROVIDER]['name']}（默认免费模型）")


def summary(path: Path | None = None) -> dict:
    data = _normalize(_read(path))
    encrypted = data.get("api_key")
    configured = isinstance(encrypted, str) and bool(encrypted)
    hint = ""
    if configured:
        try:
            hint = "••••" + _unprotect_any(encrypted)[-4:]
        except Exception:
            configured = False
    provider = data["provider"]
    preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])
    model = data.get("model") or preset.get("default_model") or ""
    effective_base = None
    try:
        effective_base = resolve_base_url(provider, data.get("base_url"))
    except ValueError:
        effective_base = data.get("base_url") or preset.get("base_url") or ""
    raw = _read(path)
    # UI always needs a visible URL. Stored base_url may be null for preset vendors;
    # expose the resolved endpoint in both fields so the form never looks empty.
    display_base = (effective_base or data.get("base_url") or preset.get("base_url") or "") or ""
    return {
        "configured": configured,
        "key_hint": hint,
        "provider": provider,
        "provider_name": preset.get("name") or provider,
        "model": model,
        "base_url": display_base if provider != "custom" else (data.get("base_url") or ""),
        "effective_base_url": display_base,
        "auto_detected": bool(raw.get("auto_detected")),
        "guess_label": str(raw.get("guess_label") or ""),
        "presets": list_presets(),
    }


def get_api_key(path: Path | None = None) -> str:
    value = _normalize(_read(path)).get("api_key")
    if not isinstance(value, str) or not value:
        raise ValueError("请先在识别服务设置中保存 API Key")
    return _unprotect_any(value)


def get_connection(path: Path | None = None) -> dict:
    data = _normalize(_read(path))
    provider = data["provider"]
    model = str(data.get("model") or "").strip()
    if not model:
        model = str(PROVIDER_PRESETS.get(provider, {}).get("default_model") or "").strip()
    if not model:
        raise ValueError("请填写模型名")
    return {
        "provider": provider,
        "api_key": get_api_key(path),
        "model": model,
        "base_url": resolve_base_url(provider, data.get("base_url")),
    }


def make_client(path: Path | None = None) -> AiClient:
    conn = get_connection(path)
    return AiClient(conn["api_key"], conn["model"], conn["base_url"], provider=conn["provider"])


def save(
    api_key: str = "",
    model: str | None = None,
    path: Path | None = None,
    *,
    provider: str | None = None,
    base_url: str | None = None,
    auto_detect: bool = False,
) -> dict:
    """Save API settings.

    Preferred teacher flow: paste API key + save with auto_detect=True.
    Later edits to model/provider can omit the key if one is already stored.

    When auto_detect=True and a new key is provided, provider/model/base_url come
    from key inference only — stale form values (e.g. old grok-4.5 + api.x.ai)
    must never win over the guessed domestic defaults.
    """
    raw_existing = _read(path)
    existing = _normalize(raw_existing)
    key = (api_key or "").strip()
    encrypted_key: str | None = None
    if key:
        if len(key) < 8:
            raise ValueError("API Key 格式不正确")
        encrypted_key = _protect(key)
    else:
        existing_key = existing.get("api_key")
        if not isinstance(existing_key, str) or not existing_key:
            raise ValueError("API Key 不能为空")
        encrypted_key = existing_key

    # New keys always run inference so legacy Grok installs cannot stick forever.
    # auto_detect=True: inference is authoritative (ignore form provider/url/model).
    # auto_detect=False with new key: still drop legacy Grok leftovers if key is not xAI.
    guessed = infer_from_api_key(key) if key else None
    is_xai_key = bool(key) and key.lower().startswith("xai-")
    legacy_store = _is_legacy_grok_settings(existing) or _is_legacy_grok_settings(raw_existing)

    if key and auto_detect and guessed:
        # Authoritative path: paste key → fill vendor/model/url from key only.
        provider_id = str(guessed["provider"]).strip()
    elif key and guessed and legacy_store and not is_xai_key:
        # New non-xAI key against old Grok settings: migrate off xAI even if UI forgot auto_detect.
        provider_id = str(provider or guessed["provider"]).strip()
    else:
        provider_id = str(provider or existing.get("provider") or DEFAULT_PROVIDER).strip()

    if provider_id not in PROVIDER_PRESETS:
        raise ValueError("不支持的服务商")
    preset = PROVIDER_PRESETS[provider_id]

    explicit_model = str(model).strip() if model is not None else ""
    existing_model = str(existing.get("model") or "").strip()
    existing_base = str(existing.get("base_url") or "").strip()

    # Drop stale Grok model/url whenever we are not intentionally configuring xAI.
    if key and not is_xai_key:
        if existing_model.lower() in LEGACY_GROK_MODELS or existing_model.lower().startswith("grok-"):
            existing_model = ""
        if "api.x.ai" in existing_base.lower():
            existing_base = ""
        if auto_detect:
            # Form may still show old values; do not treat them as teacher intent.
            if explicit_model.lower() in LEGACY_GROK_MODELS or explicit_model.lower().startswith("grok-"):
                explicit_model = ""
            if base_url is not None and "api.x.ai" in str(base_url).lower():
                base_url = None

    # Google: retire models that AI Studio now rejects for many new users.
    legacy_google_models = {
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-2.0-flash-001",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    }
    if provider_id == "google":
        if existing_model.lower() in legacy_google_models:
            existing_model = ""
        if auto_detect and explicit_model.lower() in legacy_google_models:
            explicit_model = ""
        if not auto_detect and explicit_model.lower() in legacy_google_models and not key:
            # Teacher re-saved without changing model after Google blocked it.
            pass

    if key and auto_detect and guessed:
        chosen_model = str(guessed.get("model") or preset.get("default_model") or "").strip()
        if provider_id == "custom":
            chosen_base = str(guessed.get("base_url") or "").strip() or None
        else:
            # Preset vendors always use the built-in endpoint; never persist a stale xAI URL.
            chosen_base = None
    else:
        if explicit_model:
            chosen_model = explicit_model
        elif guessed and (legacy_store and not is_xai_key) and guessed.get("model"):
            chosen_model = str(guessed["model"]).strip()
        else:
            chosen_model = existing_model or str(preset.get("default_model") or "").strip()

        if base_url is not None and str(base_url).strip():
            chosen_base = str(base_url).strip()
        elif provider_id != "custom":
            chosen_base = None
        elif guessed and not is_xai_key and legacy_store and guessed.get("base_url"):
            # Migrating off legacy: only keep guessed base when target is custom non-xAI.
            chosen_base = str(guessed["base_url"]).strip() if provider_id == "custom" else None
            if provider_id != "custom":
                chosen_base = None
        else:
            chosen_base = existing_base or None

    if provider_id == "custom" and not (chosen_base or "").strip():
        raise ValueError("自定义服务商必须填写接口地址")
    if provider_id == "custom" and not chosen_model:
        raise ValueError("自定义服务商必须填写模型名")
    resolve_base_url(provider_id, chosen_base)

    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    auto_flag = bool(key and (auto_detect or (legacy_store and not is_xai_key and guessed)))
    payload = {
        "provider": provider_id,
        "api_key": encrypted_key,
        "model": chosen_model or preset["default_model"],
        "base_url": (chosen_base or "").strip() or None,
        "auto_detected": auto_flag,
        "guess_label": (guessed or {}).get("guess_label") or "" if auto_flag else "",
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result = summary(target)
    if auto_flag and guessed:
        result["guess_label"] = guessed.get("guess_label") or ""
        result["auto_detected"] = True
    return result


def clear(path: Path | None = None) -> dict:
    target = path or settings_path()
    target.unlink(missing_ok=True)
    return summary(target)


# Backward-compatible names used by older imports/tests.
DEFAULT_MODEL = PROVIDER_PRESETS[DEFAULT_PROVIDER]["default_model"]
