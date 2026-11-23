# OPTIMUS — NVIDIA NIM Provider Integration: Reconciled Architecture Specification

## 1. Executive Summary & Reconciliation Rationale

Following the Elephant Architecture Investigation (`docs/audits/nvidia-nim/elephant-investigation.md`) and the Independent Goldfish Review (`docs/audits/nvidia-nim/goldfish-architecture-review.md`), this document formalizes the binding reconciliation plan.

The Goldfish reviewer identified critical vulnerabilities in the credential storage pipeline (plaintext storage in `config/api_keys.json`), runtime validation bricking (`missing_fields()` failing when keys are safely stored in OS Keyring), class redundancy in `core/intelligence/`, missing exception contracts (`AuthenticationError`), and logging exposure of `nvapi-...` keys.

All 7 Binding Architectural Mandates (**M-1 through M-7**) are hereby resolved and incorporated into the definitive architecture.

---

## 2. Reconciled Architectural Decisions

### Mandate M-1: Zero-Plaintext UI & Credential Bridge
- **Problem**: In `gui/providers_panel.py`, `_apply()` called `save_providers(..., creds=creds)` which persisted the user-entered `api_key` in plaintext into `config/api_keys.json`. Furthermore, `providers/registry.missing_fields()` only read `cfg.get("api_key")`, meaning omitting the key from the config file caused `core/gemini.py` to raise a fatal "404 not configured: API key" error.
- **Reconciliation**:
  1. In `gui/providers_panel.py`, when a secret field (`api_key`) is edited:
     - If the text is non-empty and not a masked sentinel (`••••••••`), immediately call `core.security.credentials.store_credential(entry_id, key)` (e.g. `entry_id = "nvidia"` or `"nvidia_nim"`).
     - Before serializing into `api_keys.json`, strip or blank out the `api_key` value in the `creds` dict.
  2. In `providers/registry.py` `missing_fields()`:
     - Update key presence check: if `cfg.get("api_key")` is empty, query `core.security.credentials.get_credential(entry)` and `get_credential(base)`. If a key is present in OS Keyring, machine vault, or `NVIDIA_API_KEY`, `missing_fields()` recognizes the provider as configured.
  3. In `gui/providers_panel.py`, when rendering `api_key`, if `stored.get("api_key")` is empty, check `get_credential(entry)`. If found, pre-populate with `••••••••` to communicate that the provider is already authenticated via the secure vault.
  4. In `providers/llm_openai.py`, resolve `self.key`:
     `self.key = (cfg.get("api_key") or get_credential(self._entry_id) or get_credential("nvidia") or "").strip()`.

### Mandate M-2: Credential Aliasing & Unified Resolution
- **Problem**: Lookups for `"nvidia"` vs `"nvidia_nim"` vs `"openai_compat@nvidia_nim"` could query different keys, and `ENV_FALLBACKS` lacked NVIDIA entries.
- **Reconciliation**:
  In `core/security/credentials.py`:
  - Update `ENV_FALLBACKS`:
    ```python
    "nvidia": ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"],
    "nvidia_nim": ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"],
    ```
  - In `store_credential(provider, secret)`:
    - If `provider` is in `("nvidia", "nvidia_nim")`, store under the canonical name `"nvidia"` and alias to `"nvidia_nim"`.
  - In `get_credential(provider)`:
    - If `provider` is in `("nvidia", "nvidia_nim")`, check canonical `"nvidia"`, then `"nvidia_nim"`, then `ENV_FALLBACKS`.
  - In `delete_credential(provider)`:
    - If `provider` is in `("nvidia", "nvidia_nim")`, remove both aliases.
  - Add `"nvidia"` to `list_configured_providers()`.

### Mandate M-3: Gateway Provider Inheritance & DRY Compliance
- **Problem**: Re-implementing a standalone `ModelProvider` in `core/intelligence/providers/nvidia_nim.py` would duplicate 250+ lines of HTTP transports, SSE chunk parsing, and message reassembly from `OpenAIProvider`.
- **Reconciliation**:
  - `NvidiaNimProvider` directly inherits from `core.intelligence.providers.openai.OpenAIProvider`.
  - In `__init__`:
    - Set default `base_url = "https://integrate.api.nvidia.com/v1"`.
    - Set `name = "nvidia_nim"`, `provider_type = ModelProviderType.NVIDIA_NIM`, and `is_local = False`.
    - Resolve `api_key` via `get_credential("nvidia") or get_credential("nvidia_nim") or os.environ.get("NVIDIA_API_KEY")`.
  - Override `_default_transport` or wrap execution to catch HTTP 400 and execute parameter stripping (Mandate M-4).

### Mandate M-4: Deterministic Parameter Stripping on HTTP 400
- **Problem**: NVIDIA NIM microservices (especially TensorRT-LLM backends) reject parameters like `presence_penalty`, `frequency_penalty`, `top_k`, or `logprobs` with HTTP 400. Blind retries cause loops.
- **Reconciliation**:
  - Disallow sending `frequency_penalty`, `presence_penalty`, `top_k`, or `logprobs` in default request payloads to NVIDIA NIM.
  - If HTTP 400 occurs, inspect the error payload with regex:
    ```python
    match = re.search(
        r"(?:unrecognized|unknown|unsupported)\s+(?:request\s+)?argument\s+['\"]?([a-zA-Z0-9_]+)['\"]?",
        err_msg,
        re.IGNORECASE,
    )
    ```
  - If a specific parameter name is captured and present in `payload`, delete it and retry the request exactly once. If no specific parameter is found or the retry fails, raise `ProviderError` / HTTP error immediately.

### Mandate M-5: Exception Contract & Immediate Circuit Breaker Tripping on 401
- **Problem**: `AuthenticationError` did not exist in `core/intelligence/types.py`. Circuit breaker counted 3 failures on 401 instead of tripping immediately.
- **Reconciliation**:
  1. Add to `core/intelligence/types.py`:
     ```python
     class AuthenticationError(ProviderError):
         """Raised when API key is missing, invalid, or rejected by upstream (HTTP 401)."""
     ```
  2. In `core/intelligence/circuit_breaker.py`:
     - Update `is_unrecoverable()` / `record_failure()`:
       If `isinstance(error, AuthenticationError)` or error text contains `"401"` or `"unauthorized"` or `"invalid bearer"`, set `self._state = CircuitState.OPEN` immediately on the very first occurrence.

### Mandate M-6: Global Secret Redaction Logging Filter
- **Problem**: `core/security/credentials.py` had no logging filter to scrub `nvapi-...` keys from logs or exception tracebacks.
- **Reconciliation**:
  In `core/security/credentials.py`:
  - Implement `SecretRedactionFilter(logging.Filter)`:
    Regex matches `nvapi-[A-Za-z0-9_\-]{16,}` and `sk-[A-Za-z0-9]{20,}` and replaces with `[REDACTED_KEY]`.
  - Implement `redact_secret(text: str) -> str` utility function.
  - Automatically install `SecretRedactionFilter` on `logging.getLogger("optimus")` and root logger.

### Mandate M-7: Complete Dual-Surface Test Coverage
- **Problem**: Test coverage must verify both the Real-Time Pipeline (`providers/`) and the Model Gateway (`core/intelligence/`), with distinct mocked and live tests.
- **Reconciliation**:
  Create two comprehensive test suites:
  1. `tests/test_nvidia_nim_gateway.py`:
     - `NvidiaNimProvider` completion, streaming, tool calls, token usage.
     - 401 fast-fail and immediate circuit breaker tripping.
     - 429 exponential backoff with jitter.
     - 400 parameter stripping on unrecognized argument.
     - `router.py` privacy blocking: `PRIVACY_STRICT` or `is_private=True` hard-blocks NVIDIA NIM.
     - Fallback chain activation when NIM trips circuit breaker.
  2. `tests/test_nvidia_nim_pipeline.py`:
     - `PRESETS["NVIDIA NIM"]` resolution to `https://integrate.api.nvidia.com/v1`.
     - `CATALOG` discovery: `"nvidia_nim"`.
     - `missing_fields()` resolving via Keyring / machine vault.
     - Zero plaintext in `config/api_keys.json` upon save.
     - UI masking and secret redaction filter verification.
     - Gated live test using `@pytest.mark.skipif(not os.environ.get("NVIDIA_API_KEY"), ...)`.

---

## 3. Implementation Plan & File Checklist

| Subsystem | Target File | Modifications |
|---|---|---|
| **Security** | `core/security/credentials.py` | Add `NVIDIA_API_KEY` to `ENV_FALLBACKS`; aliasing for `"nvidia"`/`"nvidia_nim"`; add `SecretRedactionFilter` and `redact_secret()`. |
| **Gateway Types** | `core/intelligence/types.py` | Add `ModelProviderType.NVIDIA_NIM`; add `AuthenticationError(ProviderError)`. |
| **Resilience** | `core/intelligence/circuit_breaker.py` | Trip circuit breaker immediately to `OPEN` on `AuthenticationError` / 401 / unauthorized. |
| **Gateway Provider** | `core/intelligence/providers/nvidia_nim.py` | New file: `NvidiaNimProvider` subclassing `OpenAIProvider` with parameter stripping and credential resolution. |
| **Gateway Registry** | `core/intelligence/registry.py` | Register `NvidiaNimProvider` in default providers and seed baseline models (`meta/llama-3.1-70b-instruct`, etc.). |
| **Engine Pipeline** | `providers/llm_openai.py` | Add `PRESETS["NVIDIA NIM"]`, add `nvidia_nim` to `CATALOG`, parameter sanitization, and `get_credential` fallback in `OpenAICompatLLM`. |
| **Providers Registry** | `providers/registry.py` | Update `missing_fields()` to query secure credential store. |
| **Engine UI** | `gui/providers_panel.py` | Intercept secret fields in `_apply()` to prevent writing plaintext keys to `api_keys.json`; show masked placeholder if key in Keyring. |
| **Tests** | `tests/test_nvidia_nim_gateway.py` | Comprehensive test suite for Component 5 Gateway integration. |
| **Tests** | `tests/test_nvidia_nim_pipeline.py` | Comprehensive test suite for Real-Time Pipeline and UI credential security. |

This specification is complete, robust, and directly executable.
