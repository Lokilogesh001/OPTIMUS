"""core/runtime/model_sizing.py — Hardware-Aware Model RAM & KV-Cache Sizing Calculator.

Calculates memory footprints (weights + KV cache) for local LLMs, evaluates hardware feasibility,
and computes optimal CPU thread allocations to avoid starving the OPTIMUS UI and orchestrator.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from core.runtime.profiles import HardwareProfileType
from core.runtime.resources import AdaptiveResourceManager

logger = logging.getLogger("optimus.runtime.model_sizing")


def get_optimal_cpu_threads(reserve_cores: int = 2) -> int:
    """Calculate the optimal thread count for CPU inference.
    
    Guarantees at least `reserve_cores` are left untouched for the Qt event loop,
    audio capture/synthesis, and background orchestrator tasks.
    Clamps between 1 and 4 for responsive desktop performance.
    """
    total_cores = os.cpu_count() or 4
    # For machines with 8+ cores, clamp to 4 threads; for 4 cores, use 2; minimum 1.
    allocated = max(1, total_cores - reserve_cores)
    return min(4, allocated)


def parse_model_spec(model_id: str) -> tuple[float, int]:
    """Parse parameter count in billions and quantization bit-width from model name."""
    lower = model_id.lower()
    
    # Extract parameter size: e.g. 2b, 3b, 4b, 7b, 8b, 9b, 14b, 70b
    param_match = re.search(r"(\d+(?:\.\d+)?)\s*b", lower)
    param_count_b = float(param_match.group(1)) if param_match else 3.0

    # Extract quantization: e.g. q4, q4_k_m, q5, q8, fp16
    quant_bits = 4  # standard default for GGUF/Ollama
    if "q8" in lower:
        quant_bits = 8
    elif "q5" in lower or "q5_k" in lower:
        quant_bits = 5
    elif "q6" in lower:
        quant_bits = 6
    elif "q3" in lower:
        quant_bits = 3
    elif "q2" in lower:
        quant_bits = 2
    elif "fp16" in lower or "f16" in lower:
        quant_bits = 16

    return param_count_b, quant_bits


def estimate_model_hardware_requirements(
    model_id_or_params: str | float,
    quant_bits: int | None = None,
    context_tokens: int = 4096,
) -> dict[str, Any]:
    """Calculate RAM, VRAM, KV-cache footprint, and feasibility for local execution.
    
    Args:
        model_id_or_params: Model identifier string (e.g. 'qwen3.5:2b') or float params in billions.
        quant_bits: Bit depth per parameter (default 4 if not parsed from string).
        context_tokens: Expected or configured context window size.
    """
    if isinstance(model_id_or_params, (int, float)):
        param_count_b = float(model_id_or_params)
        q_bits = quant_bits or 4
        model_name = f"{param_count_b}B-Q{q_bits}"
    else:
        model_name = str(model_id_or_params)
        parsed_p, parsed_q = parse_model_spec(model_name)
        param_count_b = parsed_p
        q_bits = quant_bits or parsed_q

    # Weight size (MB): params (billions) * (bits / 8) * 1024 / 1.0 (approx ~ 1 GB per 1B @ 8 bits)
    # Plus ~15% runtime buffer for compute graph and activation tensors
    weight_mb = (param_count_b * 1000.0) * (q_bits / 8.0) * 1.15

    # KV-Cache calculation (MB):
    # Standard formula: 2 * n_layers * n_heads * d_head * context_length * bytes_per_element
    # Scaled approximately with parameter count: ~0.15 MB per 1k context tokens per 1B params
    kv_cache_mb = (context_tokens / 1024.0) * param_count_b * 0.15 * 1024.0
    # Minimum baseline KV cache: 64 MB
    kv_cache_mb = max(64.0, kv_cache_mb)

    total_required_mb = weight_mb + kv_cache_mb

    # Hardware feasibility assessment
    res_mgr = AdaptiveResourceManager.instance()
    profile = res_mgr.get_profile()
    vitals = res_mgr.get_vitals()
    avail_ram_mb = vitals.get("free_ram_mb", 4096.0)

    recommended_threads = get_optimal_cpu_threads()

    is_feasible = True
    reason = "Hardware resources adequate for execution."

    if profile.profile_type == HardwareProfileType.CPU_ONLY:
        # In CPU_ONLY mode, all weights and KV cache reside in system RAM.
        # Leave at least 1.5 GB for OS and OPTIMUS UI
        safe_budget_mb = max(512.0, avail_ram_mb - 1536.0)
        if total_required_mb > safe_budget_mb:
            is_feasible = False
            reason = (
                f"Model requires ~{total_required_mb:.0f} MB RAM (weights: {weight_mb:.0f} MB, "
                f"KV: {kv_cache_mb:.0f} MB), but only {avail_ram_mb:.0f} MB is available (safe budget: {safe_budget_mb:.0f} MB)."
            )
    else:
        # GPU or Hybrid: If VRAM insufficient, offloads to RAM
        vram_avail_mb = vitals.get("gpu_vram_total_mb", getattr(profile, "vram_mb", 0.0))
        if vram_avail_mb > 0 and total_required_mb > (vram_avail_mb + avail_ram_mb):
            is_feasible = False
            reason = f"Combined VRAM and RAM insufficient for model ({total_required_mb:.0f} MB required)."

    return {
        "model_id": model_name,
        "param_count_b": param_count_b,
        "quant_bits": q_bits,
        "context_tokens": context_tokens,
        "weight_ram_mb": round(weight_mb, 1),
        "kv_cache_mb": round(kv_cache_mb, 1),
        "total_required_ram_mb": round(total_required_mb, 1),
        "available_ram_mb": round(avail_ram_mb, 1),
        "is_feasible": is_feasible,
        "recommended_threads": recommended_threads,
        "recommended_quant": "q4_K_M" if q_bits >= 4 else "q2_K",
        "reason": reason,
    }
