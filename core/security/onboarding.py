"""core/security/onboarding.py — Onboarding State, Provider Verification & Configuration Persistence.

Manages:
- First-launch detection and onboarding completion state.
- Separation of operational configuration from conversation memory/task state.
- Live endpoint/key connection verification across all supported providers (Gemini, OpenAI, Anthropic, OpenRouter, Groq, Custom, Ollama).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.security.credentials import get_credential
from core.security.permissions import get_permission_manager

logger = logging.getLogger("optimus.security.onboarding")

DEFAULT_SETTINGS_PATH = Path.cwd() / "config" / "optimus_settings.json"

PROVIDER_METADATA: dict[str, dict[str, Any]] = {
    "gemini": {
        "display_name": "Google Gemini",
        "description": "Multimodal intelligence with live audio streaming & tool use.",
        "default_model": "gemini-2.0-flash",
        "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash", "models/gemini-3.1-flash-live-preview"],
        "requires_key": True,
        "requires_endpoint": False,
        "default_endpoint": "https://generativelanguage.googleapis.com",
    },
    "openai": {
        "display_name": "OpenAI",
        "description": "Industry-standard GPT-4o, structured outputs, and deep reasoning.",
        "default_model": "gpt-4o",
        "models": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
        "requires_key": True,
        "requires_endpoint": False,
        "default_endpoint": "https://api.openai.com/v1",
    },
    "anthropic": {
        "display_name": "Anthropic Claude",
        "description": "Claude 3.5 Sonnet / Haiku with nuanced reasoning and code capabilities.",
        "default_model": "claude-3-5-sonnet-20241022",
        "models": ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
        "requires_key": True,
        "requires_endpoint": False,
        "default_endpoint": "https://api.anthropic.com/v1",
    },
    "openrouter": {
        "display_name": "OpenRouter",
        "description": "Unified access to open-weights & proprietary models via single API.",
        "default_model": "anthropic/claude-3.5-sonnet",
        "models": ["anthropic/claude-3.5-sonnet", "openai/gpt-4o", "meta-llama/llama-3.3-70b-instruct", "deepseek/deepseek-chat"],
        "requires_key": True,
        "requires_endpoint": False,
        "default_endpoint": "https://openrouter.ai/api/v1",
    },
    "groq": {
        "display_name": "Groq Cloud",
        "description": "Ultra-low-latency LPU inference for real-time interaction.",
        "default_model": "llama-3.3-70b-versatile",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
        "requires_key": True,
        "requires_endpoint": False,
        "default_endpoint": "https://api.groq.com/openai/v1",
    },
    "custom": {
        "display_name": "Custom OpenAI-Compatible",
        "description": "Local or hosted OpenAI-compatible inference (vLLM, LM Studio, LocalAI).",
        "default_model": "default",
        "models": ["default", "local-model"],
        "requires_key": False,
        "requires_endpoint": True,
        "default_endpoint": "http://localhost:8000/v1",
    },
    "local_only": {
        "display_name": "Local-Only (Ollama)",
        "description": "100% private, on-device execution without external cloud connectivity.",
        "default_model": "qwen2.5:7b",
        "models": ["qwen2.5:7b", "llama3.2:3b", "deepseek-r1:8b", "mistral:7b"],
        "requires_key": False,
        "requires_endpoint": True,
        "default_endpoint": "http://localhost:11434",
    },
}


def is_onboarded(settings_path: Path | None = None) -> bool:
    """Check if OPTIMUS has completed initial setup and onboarding."""
    if settings_path is not None:
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
                return bool(data.get("onboarding_completed"))
            except Exception:
                return False
        return False

    path = DEFAULT_SETTINGS_PATH
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("onboarding_completed"):
                return True
        except Exception:
            pass

    # If legacy config/api_keys.json exists and has a configured key or local_only
    legacy_path = Path.cwd() / "config" / "api_keys.json"
    if legacy_path.exists():
        try:
            leg_data = json.loads(legacy_path.read_text(encoding="utf-8"))
            if leg_data.get("gemini_api_key") or leg_data.get("local_only"):
                return True
        except Exception:
            pass

    return False


def get_onboarding_config(settings_path: Path | None = None) -> dict[str, Any]:
    """Retrieve active onboarding and operational configuration."""
    path = settings_path or DEFAULT_SETTINGS_PATH
    data: dict[str, Any] = {
        "onboarding_completed": False,
        "primary_provider": "gemini",
        "model_name": "gemini-2.0-flash",
        "custom_endpoint": "",
        "fallback_provider": "local_only",
        "fallback_model": "qwen2.5:7b",
        "permission_profile": "ADVANCED",
        "workspace_roots": [str(Path.cwd())],
    }

    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            data.update(loaded)
        except Exception as e:
            logger.warning(f"Failed to read settings: {e}")

    return data


def save_onboarding_config(cfg: dict[str, Any], settings_path: Path | None = None) -> bool:
    """Persist operational configuration, separate from memory/task stores."""
    path = settings_path or DEFAULT_SETTINGS_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        current = get_onboarding_config(path)
        current.update(cfg)
        current["onboarding_completed"] = True
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")

        # Update permission manager
        if "permission_profile" in cfg:
            get_permission_manager().set_profile(cfg["permission_profile"])

        return True
    except Exception as e:
        logger.error(f"Failed to save onboarding config: {e}")
        return False


def verify_provider_connection(
    provider: str,
    api_key: str | None = None,
    endpoint: str | None = None,
    model: str | None = None,
    timeout: float = 6.0,
) -> tuple[bool, str]:
    """Verify provider connectivity and credentials before finalizing configuration."""
    prov = provider.strip().lower()
    meta = PROVIDER_METADATA.get(prov, {})
    url = (endpoint or meta.get("default_endpoint", "")).rstrip("/")
    key = api_key or get_credential(prov) or ""

    if prov == "local_only":
        # Test Ollama API
        check_url = f"{url}/api/tags" if not url.endswith("/api/tags") else url
        try:
            req = urllib.request.Request(check_url, headers={"User-Agent": "OPTIMUS-Client"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = [m.get("name") for m in data.get("models", [])]
                    return True, f"Connected to Ollama. {len(models)} local models found."
        except Exception as e:
            return False, f"Ollama connection failed ({e}). Is Ollama running on {url}?"

    elif prov == "custom":
        # Test custom OpenAI-compatible endpoint
        check_url = f"{url}/models" if not url.endswith("/models") else url
        try:
            headers = {"User-Agent": "OPTIMUS-Client"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            req = urllib.request.Request(check_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 201):
                    return True, f"Connected to custom endpoint at {url}."
        except Exception as e:
            return False, f"Custom endpoint verification failed ({e}) at {check_url}."

    elif prov == "gemini":
        if not key:
            return False, "Gemini API key is required."
        check_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        try:
            req = urllib.request.Request(check_url, headers={"User-Agent": "OPTIMUS-Client"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True, "Google Gemini API key verified successfully."
        except urllib.error.HTTPError as he:
            return False, f"Gemini API returned HTTP {he.code}: {he.reason}"
        except Exception as e:
            return False, f"Gemini verification error: {e}"

    elif prov in ("openai", "groq", "openrouter"):
        if not key:
            return False, f"{meta.get('display_name', prov)} API key is required."
        check_url = f"{url}/models"
        try:
            headers = {
                "User-Agent": "OPTIMUS-Client",
                "Authorization": f"Bearer {key}",
            }
            if prov == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/Lokilogesh001/OPTIMUS"
                headers["X-Title"] = "OPTIMUS Autonomous AI"
            req = urllib.request.Request(check_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True, f"{meta.get('display_name', prov)} credentials verified successfully."
        except urllib.error.HTTPError as he:
            return False, f"{meta.get('display_name', prov)} returned HTTP {he.code}: {he.reason}"
        except Exception as e:
            return False, f"Verification failed: {e}"

    elif prov == "anthropic":
        if not key:
            return False, "Anthropic API key is required."
        # Anthropic doesn't have an open /models endpoint without headers, test headers
        check_url = "https://api.anthropic.com/v1/messages"
        payload = json.dumps({
            "model": model or "claude-3-5-haiku-20241022",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                check_url,
                data=payload,
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                    "User-Agent": "OPTIMUS-Client",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 201):
                    return True, "Anthropic API key verified successfully."
        except urllib.error.HTTPError as he:
            if he.code == 401:
                return False, "Invalid Anthropic API key."
            # Even if quota / invalid model (400), key authentication passed
            if he.code in (400, 429):
                return True, f"Anthropic authentication accepted (HTTP {he.code})."
            return False, f"Anthropic API returned HTTP {he.code}: {he.reason}"
        except Exception as e:
            return False, f"Anthropic verification error: {e}"

    return True, "Verification completed."
