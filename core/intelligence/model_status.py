"""core/intelligence/model_status.py — Authoritative Active Model & Configuration Inspector.

Provides deterministic runtime inspection of the active provider, configured model,
active serving model, fallback state, configuration source, and verification status
without guessing from memory notes, conversations, or web search.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("optimus.intelligence.model_status")


def _default_settings_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "config" / "optimus_settings.json"


def _default_api_keys_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "config" / "api_keys.json"


def get_active_model_status(
    *,
    runtime_live_model: str | None = None,
    settings_path: Path | str | None = None,
    api_keys_path: Path | str | None = None,
    gateway: Any | None = None,
) -> dict[str, Any]:
    """Inspect authoritative runtime and config sources for active AI model state."""
    sources: list[str] = []

    # 1. Read persisted settings cleanly
    s_path = Path(settings_path) if settings_path else _default_settings_path()
    settings: dict[str, Any] = {}
    if s_path.is_file():
        try:
            raw_s = json.loads(s_path.read_text(encoding="utf-8"))
            if isinstance(raw_s, dict):
                settings = raw_s
                sources.append(str(s_path))
        except Exception as exc:
            logger.debug("Could not parse settings file %s: %s", s_path, exc)

    k_path = Path(api_keys_path) if api_keys_path else _default_api_keys_path()
    api_cfg: dict[str, Any] = {}
    if k_path.is_file():
        try:
            raw_k = json.loads(k_path.read_text(encoding="utf-8"))
            if isinstance(raw_k, dict):
                api_cfg = raw_k
                sources.append(str(k_path))
        except Exception as exc:
            logger.debug("Could not parse api_keys file %s: %s", k_path, exc)

    # 2. Configured defaults from authoritative config + core.gemini
    local_only = bool(api_cfg.get("local_only", False))
    sidecar_prov = str(api_cfg.get("sidecar_provider") or api_cfg.get("sidecar") or "").strip()
    providers_cfg = api_cfg.get("providers") if isinstance(api_cfg.get("providers"), dict) else {}

    primary_provider = str(settings.get("primary_provider") or "").strip().lower()
    if not primary_provider and local_only:
        primary_provider = "local_only"
    elif not primary_provider and (api_cfg.get("gemini_api_key") or "model_name" in settings):
        primary_provider = "gemini" if settings.get("model_name", "").startswith(("models/gemini", "gemini")) or api_cfg.get("gemini_api_key") else "unknown"

    configured_model = str(settings.get("model_name") or "").strip()
    fallback_provider = str(settings.get("fallback_provider") or "").strip()
    fallback_model = str(settings.get("fallback_model") or "").strip()

    live_ladder: tuple[str, ...] = ()
    gemini_live_fn = None
    gemini_ladder_primary = ""
    try:
        from core import gemini as _gemini
        live_ladder = tuple(getattr(_gemini, "LIVE_MODELS", ()) or ())
        gemini_live_fn = getattr(_gemini, "live_model", None)
        if live_ladder:
            gemini_ladder_primary = live_ladder[0]
            sources.append("core/gemini.py:LIVE_MODELS")
    except Exception:
        pass

    if not configured_model and gemini_ladder_primary and primary_provider in ("gemini", ""):
        configured_model = gemini_ladder_primary
        if not primary_provider:
            primary_provider = "gemini"

    # 3. Runtime active live model (authoritative when voice session in main is active)
    live_mod = sys.modules.get("main")
    live_attr = getattr(live_mod, "LIVE_MODEL", None) if live_mod is not None else None
    runtime_live = str(runtime_live_model or live_attr or "").strip()
    if runtime_live_model:
        sources.append("runtime:explicit_live_model")
    elif live_attr:
        sources.append("runtime:main.LIVE_MODEL")

    # Check live_model() cooldown selection when Gemini is configured
    gemini_selected_live = ""
    if callable(gemini_live_fn) and primary_provider == "gemini":
        try:
            gemini_selected_live = str(gemini_live_fn() or "")
        except Exception:
            gemini_selected_live = ""

    # 4. IntelligenceGateway inspection (for non-Live orchestrator/agent models)
    gw_summary: dict[str, Any] = {}
    gw_selected_provider = ""
    gw_selected_model = ""
    gw_policy = ""
    try:
        if gateway is None:
            from core.intelligence.gateway import get_intelligence_gateway
            gateway = get_intelligence_gateway()
        gw_policy = str(getattr(gateway.router, "policy", "") or "")
        if hasattr(gateway.router, "policy") and hasattr(gateway.router.policy, "value"):
            gw_policy = gateway.router.policy.value
        from core.intelligence.types import ModelRequest
        candidates = gateway.router.select_models(ModelRequest(is_private=local_only))
        if candidates:
            top = candidates[0]
            gw_selected_model = top.model_id
            gw_selected_provider = top.provider_type.value if hasattr(top.provider_type, "value") else str(top.provider_type)
        gw_summary = {
            "policy": gw_policy,
            "top_candidate_model": gw_selected_model or None,
            "top_candidate_provider": gw_selected_provider or None,
        }
        sources.append("IntelligenceGateway")
    except Exception as exc:
        logger.debug("Gateway inspection skipped: %s", exc)

    # 5. Reconcile active provider, active model, fallback status, and verification status
    active_provider = primary_provider or gw_selected_provider or "unknown"
    active_model = ""
    active_client = ""
    fallback_activated = False
    verification_status = "verified"
    verification_reason = ""

    if local_only or active_provider in ("ollama", "local_only", "local"):
        # Local/Ollama configured explicitly
        ollama_cfg = providers_cfg.get("ollama") if isinstance(providers_cfg.get("ollama"), dict) else {}
        local_model = (
            str(ollama_cfg.get("model") or "").strip()
            or (configured_model if configured_model and "gemini" not in configured_model.lower() else "")
            or fallback_model
            or gw_selected_model
        )
        active_provider = "ollama" if active_provider in ("ollama", "local_only", "local") else active_provider
        active_model = local_model or "unknown"
        active_client = "OllamaProvider"
        if not local_model:
            verification_status = "unverified"
            verification_reason = "No local model identifier is configured."
        else:
            verification_reason = "Resolved from local/Ollama runtime configuration."
    elif runtime_live:
        active_provider = "gemini" if "gemini" in runtime_live.lower() else active_provider
        active_model = runtime_live
        active_client = "google.genai.live"
        default_cmp = configured_model or gemini_ladder_primary
        if default_cmp and runtime_live != default_cmp:
            fallback_activated = True
        verification_reason = "Verified directly against live runtime session model."
    elif primary_provider == "gemini":
        if gemini_selected_live:
            active_model = gemini_selected_live
            active_client = "core.gemini (LIVE/REST ladder)"
            if gemini_ladder_primary and gemini_selected_live != gemini_ladder_primary:
                fallback_activated = True
            verification_status = "unverified"
            verification_reason = (
                "No active main.LIVE_MODEL session handle in this process; "
                "reporting configured Gemini model and next healthy ladder rung."
            )
        elif configured_model:
            active_model = configured_model
            active_client = "config/optimus_settings.json"
            verification_status = "unverified"
            verification_reason = "Configured model read from settings; live serving session not attached."
        else:
            active_model = "unknown"
            active_client = "none"
            verification_status = "unverified"
            verification_reason = "No configured or live Gemini model identifier found."
    elif configured_model or gw_selected_model:
        active_model = gw_selected_model or configured_model
        active_client = f"IntelligenceGateway ({gw_selected_provider or active_provider})"
        if fallback_model and active_model == fallback_model and configured_model and configured_model != fallback_model:
            fallback_activated = True
        verification_status = "verified" if gw_selected_model else "unverified"
        verification_reason = (
            "Resolved from IntelligenceGateway router."
            if gw_selected_model
            else "Read from settings; live serving client unverified."
        )
    else:
        active_provider = "unknown"
        active_model = "unknown"
        active_client = "none"
        verification_status = "unverified"
        verification_reason = "No provider or model configuration found."

    if sidecar_prov and not local_only:
        sources.append(f"sidecar:{sidecar_prov}")

    configured_display = configured_model or "unconfigured"
    differs = bool(
        configured_model
        and active_model
        and active_model != "unknown"
        and active_model != configured_model
    )
    if differs and fallback_model and active_model == fallback_model:
        fallback_activated = True

    return {
        "provider": active_provider,
        "configured_model_id": configured_display,
        "active_model_id": active_model,
        "active_client_or_engine": active_client,
        "fallback_provider": fallback_provider or None,
        "fallback_model": fallback_model or (live_ladder[1] if len(live_ladder) > 1 and active_provider == "gemini" else None),
        "fallback_activated": fallback_activated,
        "differs_from_configured_default": differs,
        "config_source": sources,
        "verification_status": verification_status,
        "verification_reason": verification_reason,
        "gateway": gw_summary,
    }


def format_active_model_report(status: dict[str, Any]) -> str:
    """Render concise deterministic model & configuration summary."""
    return (
        f"Active Provider: {status.get('provider')}\n"
        f"Configured Model: {status.get('configured_model_id')}\n"
        f"Active Model: {status.get('active_model_id')}\n"
        f"Active Client/Engine: {status.get('active_client_or_engine')}\n"
        f"Fallback Model: {status.get('fallback_model') or 'None'}\n"
        f"Fallback Activated: {status.get('fallback_activated')}\n"
        f"Differs From Default: {status.get('differs_from_configured_default')}\n"
        f"Verification Status: {status.get('verification_status')} ({status.get('verification_reason')})\n"
        f"Config Source: {', '.join(status.get('config_source') or []) or 'none'}"
    )
