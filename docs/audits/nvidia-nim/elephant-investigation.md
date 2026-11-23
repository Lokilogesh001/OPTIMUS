# OPTIMUS — NVIDIA NIM Provider Integration: Elephant Investigation & Architecture Specification

## 1. Executive Summary & Objective

The objective of this engineering initiative is to integrate **NVIDIA NIM** (NVIDIA Inference Microservices) as a first-class inference provider across the OPTIMUS application.

OPTIMUS possesses two interconnected inference surfaces:
1. **The Real-Time Conversation Pipeline (`providers/`)**: Powers the continuous voice, desktop listening, conversation streaming, and tool execution loop (`engine_pipeline.py`), configurable via the **Engine & Providers UI** (`gui/providers_panel.py`).
2. **The Model Intelligence Gateway (`core/intelligence/`)**: Powers central orchestrator tasks, background subagents, multi-tier fallback routing, and structured reasoning (`gateway.py`, `router.py`, `registry.py`).

This specification defines the seamless integration of NVIDIA NIM into both surfaces without parallelizing or duplicating abstractions, preserving all existing providers (Gemini Live, Ollama, LM Studio, llama.cpp, vLLM, OpenAI, Anthropic, Groq, etc.), and strictly enforcing zero-plaintext credential security.

---

## 2. Upstream NVIDIA NIM Specification & Operational Reality

### 2.1 Endpoint Specification
- **Base URL**: `https://integrate.api.nvidia.com/v1`
- **Protocol**: OpenAI-compatible REST API
- **Chat Completions**: `POST https://integrate.api.nvidia.com/v1/chat/completions`
- **Model Discovery**: `GET https://integrate.api.nvidia.com/v1/models`
- **Authentication**: Bearer Token via HTTP Header: `Authorization: Bearer nvapi-...`

### 2.2 Operational Idiosyncrasies & Defensive Handling
1. **Model Identifiers**: NVIDIA NIM organizes models by publisher and name with slashes, e.g.:
   - `meta/llama-3.1-70b-instruct`
   - `meta/llama-3.1-8b-instruct`
   - `mistralai/mixtral-8x22b-instruct-v0.1`
   - `nvidia/nemotron-4-340b-instruct`
   The UI and adapter must allow both dynamic discovery via `/models` and arbitrary manual entry.
2. **Parameter Divergence**: Different NIM microservices are hosted on varying backend inference runtimes (TensorRT-LLM, vLLM, Triton). Some models reject non-standard parameters (e.g. `frequency_penalty`, `presence_penalty`, `logprobs`, `top_k`) with `400 Bad Request` (`"Unrecognized request argument"`).
   - *Design Mandate*: The adapter must sanitize payloads, pass standard parameters (`messages`, `model`, `temperature`, `max_tokens`, `top_p`, `tools`, `tool_choice`, `stream`), and provide an automatic parameter-stripping retry if a 400 error flags an unrecognized parameter.
3. **Streaming Protocol**: Emits standard SSE (`data: {"choices": [{"delta": {"content": "..."}}]}`). Stream termination is signified by `data: [DONE]`. Tool call chunks arrive with index-keyed delta fragments.

---

## 3. Architecture & System Integration

### 3.1 Real-Time Engine Integration (`providers/`)
- **`providers/llm_openai.py`**:
  - Add preset: `"NVIDIA NIM": "https://integrate.api.nvidia.com/v1"` in `PRESETS`.
  - Add catalog entry in `CATALOG`:
    ```python
    {
        "id": "nvidia_nim",
        "label": "NVIDIA NIM",
        "note": "cloud · high-performance accelerated inference · needs key",
        "local": False,
        "values": {
            "base_url": PRESETS["NVIDIA NIM"],
            **_CLOUD,
            "model": "meta/llama-3.1-70b-instruct",
        },
    }
    ```
  - In `OpenAICompatLLM`:
    - Automatically resolve API key if not provided: check `core.security.credentials.get_credential("nvidia")` or `os.environ.get("NVIDIA_API_KEY")`.
    - Support parameter stripping on 400 parameter errors.

### 3.2 Engine & Providers UI (`gui/providers_panel.py`)
- Automatically populated through `CATALOG` entry `"nvidia_nim"`.
- When "NVIDIA NIM" is selected:
  - Base URL defaults to `https://integrate.api.nvidia.com/v1`.
  - Model field shows suggested models with manual override capability.
  - API Key field is marked `required=True` with masked input.
  - "TEST" probe executes verification completion (`"Reply with the single word: ok"`), checks vision capability, and displays response latency and token stats.
  - "REFRESH" query calls `/v1/models` and populates the model dropdown with active NIM endpoints.
  - "Set as Default" / APPLY persists configuration atomically through `save_providers()`.

### 3.3 Model Intelligence Gateway Integration (`core/intelligence/`)
- **`core/intelligence/types.py`**:
  - Add `NVIDIA_NIM = "NVIDIA_NIM"` to `ModelProviderType`.
- **`core/intelligence/providers/nvidia_nim.py`**:
  - Implement concrete `NvidiaNimProvider(ModelProvider)`:
    - `complete(request: ModelRequest) -> ModelResponse`
    - `stream(request: ModelRequest) -> Iterator[StreamChunk]`
    - `health_check() -> bool`
    - `list_models() -> list[ModelMetadata]`
    - Support function/tool calling and JSON schema serialization.
    - Integrated with `CircuitBreaker`.
- **`core/intelligence/registry.py`**:
  - Register `NvidiaNimProvider` in `ModelRegistry._seed_default_providers()`.
  - Seed baseline NIM models with capabilities tags (`CHAT`, `STREAMING`, `TOOL_CALLING`, `STRUCTURED_OUTPUT`).
- **`core/intelligence/router.py`**:
  - Participates in standard policy-based routing:
    - Filtered out during `PRIVACY_STRICT` or `is_private=True` tasks (cloud boundary enforcement).
    - Ranked high during `PERFORMANCE_OPTIMIZED` and `CLOUD_PREFERRED` tasks.
    - Available as reliable fallback in multi-tier fallback chains.

---

## 4. Credential Security & Redaction Architecture

1. **OS-Backed Key Storage**:
   - Primary: Windows Credential Manager / OS Keyring via `core/security/credentials.py`.
   - Secondary Fallback: Machine-bound encrypted vault (`.vault.enc` via Fernet PBKDF2HMAC).
   - Tertiary Fallback: Environment variable `NVIDIA_API_KEY`.
2. **Zero-Plaintext Invariant**:
   - No API keys committed to Git, written to `api_keys.json`, or exposed in logs.
   - When stored through UI, API key is committed directly to Keyring / vault via `store_credential("nvidia", key)`.
3. **Secret Redaction**:
   - `mask_credential(secret)` displays `nvapi-...4a1b` or `••••••••`.
   - Logging redaction filter and exception handler redact any string matching `nvapi-[A-Za-z0-9_-]{16,}`.

---

## 5. Resilience, Error Normalization & Circuit Breaking

| Error Case | HTTP Status | Action / Normalization |
|---|---|---|
| **Invalid API Key** | `401 Unauthorized` | Raise `AuthenticationError` / fatal probe rejection. Do NOT retry; do NOT enter infinite retry loops. Clear UI error: "The server refused the key." |
| **Model Not Found / Inactive** | `404 Not Found` | Raise `ProviderError(f"No model called '{model}'")`. Route to next fallback model. |
| **Rate Limit / Concurrency** | `429 Too Many Requests` | Raise `RateLimitError`. Exponential backoff with jitter (up to 2 retries). If quota exhausted, trip circuit breaker. |
| **Unsupported Parameter** | `400 Bad Request` | Strip offending parameter (`presence_penalty`, etc.) and retry once. |
| **Connection Timeout / 5xx** | `502/503/504 / URLError` | Raise `TimeoutError` / `ProviderError`. Record failure in `CircuitBreaker`. Router automatically selects secondary fallback provider. |

---

## 6. Acceptance Testing Strategy

1. **Mocked Unit & Integration Suite (`tests/test_nvidia_nim_provider.py`)**:
   - `test_nvidia_nim_complete_success`: Valid mock response, token usage, text extraction.
   - `test_nvidia_nim_tool_calling`: Tool call serialization and parsing into `ToolCall`.
   - `test_nvidia_nim_streaming`: SSE stream chunk decoding.
   - `test_nvidia_nim_auth_failure_401`: Immediate fatal handling without infinite retries.
   - `test_nvidia_nim_rate_limit_429`: Bounded backoff and circuit breaker transition.
   - `test_nvidia_nim_parameter_sanitization_400`: Automatic parameter stripping on rejection.
   - `test_nvidia_nim_credential_resolution_order`: Keyring -> Vault -> Env var (`NVIDIA_API_KEY`).
   - `test_nvidia_nim_secret_redaction`: Masking in logs and exceptions.
   - `test_nvidia_nim_router_fallback_chain`: Seamless fallback to secondary provider when NIM trips circuit breaker.
   - `test_nvidia_nim_privacy_strict_cloud_block`: Hard block when request is flagged private.
2. **Provider Pipeline & Catalog Suite**:
   - `test_nvidia_nim_catalog_discovery`: Verification of `providers/registry.py` and `providers/llm_openai.py` catalog entry.
   - `test_nvidia_nim_probe_verification`: Health probe and latency checks.
3. **Regression Safety**:
   - Verify all existing providers (Ollama, OpenAI, Anthropic, Gemini Live) continue to pass 100%.
