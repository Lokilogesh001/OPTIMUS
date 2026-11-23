# OPTIMUS — NVIDIA NIM Provider Integration: Independent Goldfish Architecture & Security Review

**Reviewer**: Independent Goldfish Security & Architecture Reviewer (EGM)  
**Target Document**: `docs/audits/nvidia-nim/elephant-investigation.md`  
**Date**: 2026-11-23  
**Verdict**: **CONDITIONAL PASS** (Blocked on Binding Architectural Mandates M-1 through M-7)

---

## 1. Executive Summary

An exhaustive, adversarial audit of the Elephant Architecture Specification for NVIDIA NIM Provider Integration (`docs/audits/nvidia-nim/elephant-investigation.md`) was conducted against the active codebase across both inference surfaces:
1. **The Real-Time Engine & Conversation Pipeline**: `providers/llm_openai.py`, `providers/registry.py`, `providers/base.py`, `gui/providers_panel.py`, and `memory/config_manager.py`.
2. **The Model Intelligence Gateway**: `core/intelligence/types.py`, `core/intelligence/registry.py`, `core/intelligence/router.py`, `core/intelligence/providers/openai.py`, and `core/intelligence/providers/base.py`.
3. **Security Infrastructure**: `core/security/credentials.py` and `core/intelligence/circuit_breaker.py`.

### Overall Assessment
The Elephant specification establishes a commendable high-level vision: cleanly anchoring NVIDIA NIM within the existing OpenAI-compatible pipeline infrastructure rather than inventing duplicate provider hierarchies. However, critical architectural omissions and flawed assumptions were uncovered in the credential storage pipeline, error handling contracts, parameter sanitization, and registry validation logic. 

Most critically, **the Elephant specification claims a "zero-plaintext invariant" while specifying a UI persistence workflow (`save_providers()`) that directly writes plaintext API keys into `config/api_keys.json`**. Furthermore, if the key were removed from `api_keys.json` to satisfy security, `providers/registry.missing_fields()` would immediately flag the provider as unconfigured, bricking the conversation engine and the `core/gemini.py` fallback ladder.

These flaws must be reconciled prior to code implementation.

---

## 2. Detailed Findings by Evaluation Criteria

### Criterion 1: Architectural Soundness & Non-Redundancy
- **Rating**: **CONDITIONAL PASS**
- **Analysis**:
  - **Surface 1 (Real-Time Conversation Pipeline)**: Extending `providers/llm_openai.py` with `PRESETS["NVIDIA NIM"]` and adding a catalog descriptor (`"id": "nvidia_nim"`) in `CATALOG` is architecturally optimal. It leverages the existing 13-service abstraction, avoids creating a parallel `providers/llm_nvidia.py`, and integrates directly with `engine_pipeline.py`.
  - **Surface 2 (Model Intelligence Gateway)**: The specification proposes creating `core/intelligence/providers/nvidia_nim.py` implementing `ModelProvider`. However, the spec leaves it as an independent implementation of `ModelProvider` rather than subclassing `core.intelligence.providers.openai.OpenAIProvider`. Because NVIDIA NIM adheres to the `/v1/chat/completions` wire contract, reimplementing HTTP transports, SSE delta reassembly, and message reshaping from scratch creates redundant boilerplate and parallel bugs in `core/intelligence/`.
- **Severity**: **MEDIUM** (See Mandate **M-3**).

---

### Criterion 2: Credential Security & Redaction Invariant
- **Rating**: **FAIL (Blocker)**
- **Analysis**:
  1. **Plaintext API Key Leakage via GUI Workflow (`CRITICAL`)**:
     The Elephant spec states:
     > *"No API keys committed to Git, written to `api_keys.json`, or exposed in logs. When stored through UI, API key is committed directly to Keyring / vault via `store_credential("nvidia", key)`."*
     
     In `gui/providers_panel.py` (lines 1132–1143), clicking APPLY gathers all edits via `self._values(sid)` and calls `save_providers(..., creds=creds)`. `self._values()` reads the raw text from the `api_key` input field. `save_providers()` in `memory/config_manager.py` (lines 674–681) serializes `creds` directly into `config/api_keys.json` under `providers.creds[provider_id]["api_key"]`!
     The Elephant specification provides **no mechanism** in `providers_panel.py` or `save_providers()` to intercept secret fields. As written, any user configuring NVIDIA NIM in the UI will write `nvapi-...` in plaintext to `api_keys.json`.
  2. **Runtime Brick via `missing_fields()` (`CRITICAL`)**:
     In `providers/registry.py` (lines 192–195):
     ```python
     if (catalog_entry(provider_id).get("local") is False
             and not str(cfg.get("api_key", "")).strip()):
         out.append("API key")
     ```
     If the API key is withheld from `api_keys.json` (to preserve the zero-plaintext rule), `missing_fields()` returns `["API key"]`. In `core/gemini.py` (lines 705–707):
     ```python
     missing = registry.missing_fields(pid, cfg)
     if missing:
         raise RuntimeError(f"404 not configured: {', '.join(missing)}")
     ```
     The sidecar ladder and coding agent will refuse to invoke NVIDIA NIM, raising a fatal 404!
  3. **Keyring Identifier & Environment Key Inconsistency (`HIGH`)**:
     The spec uses `"nvidia_nim"` as the catalog ID, `"openai_compat@nvidia_nim"` as the service ID, and `ModelProviderType.NVIDIA_NIM = "NVIDIA_NIM"`, but specifies `store_credential("nvidia", key)` and `get_credential("nvidia")`.
     In `core/security/credentials.py`, `ENV_FALLBACKS` does not define `"nvidia"` or `"nvidia_nim"`. Calling `get_credential("nvidia_nim")` falls back to `f"{provider_clean.upper()}_API_KEY"` (`NVIDIA_NIM_API_KEY`), ignoring standard `NVIDIA_API_KEY`.
  4. **Unregistered Secret Redactor (`MEDIUM`)**:
     The spec mandates regex redacting for `nvapi-[A-Za-z0-9_-]{16,}` in logs and exceptions, but `core/security/credentials.py` only implements `mask_credential(secret)` for UI display. No logging filter exists on Python's `logging` handlers.
- **Severity**: **BLOCKER** (See Mandates **M-1, M-2, M-6**).

---

### Criterion 3: Parameter Compatibility & Heterogeneous Model Quirks
- **Rating**: **CONDITIONAL PASS**
- **Analysis**:
  - NVIDIA NIM hosts diverse backends (TensorRT-LLM, Triton, vLLM). Open-source models (Llama 3.1, Nemotron, Mistral) reject non-standard parameters (`frequency_penalty`, `presence_penalty`, `top_k`, `logprobs`) with HTTP 400 (`"Unrecognized request argument"`).
  - Furthermore, in `core/intelligence/providers/openai.py`, structured output sends:
    `payload["response_format"] = {"type": "json_schema", "json_schema": {"schema": req.response_schema}}`.
    Many NIM endpoints only support `response_format: {"type": "json_object"}` or reject `response_format` entirely when using TensorRT-LLM.
  - The Elephant spec states: *"Strip offending parameter and retry once"*, but fails to provide the parsing contract for extracting the offending parameter name from error payloads. Blind retries without parameter extraction risk infinite loops or mask valid client validation errors.
- **Severity**: **MEDIUM** (See Mandate **M-4**).

---

### Criterion 4: Resilience, Error Normalization & Circuit Breaking
- **Rating**: **CONDITIONAL PASS**
- **Analysis**:
  1. **Missing `AuthenticationError` Contract (`HIGH`)**:
     Table 5 of the Elephant spec mandates raising `AuthenticationError` on HTTP 401. However, `AuthenticationError` **does not exist** in `core/intelligence/types.py` (which only defines `IntelligenceError`, `ProviderError`, `TimeoutError`, `RateLimitError`, `PrivacyViolationError`, `CircuitOpenError`, `NoAvailableModelError`). Raising `AuthenticationError` would trigger an unhandled `NameError`.
  2. **Circuit Breaker Trip Failure on 401 (`HIGH`)**:
     In `core/intelligence/circuit_breaker.py` (line 88), `is_unrecoverable` only inspects:
     `("insufficient_quota", "credit_balance_exhausted", "quota", "auth failed", "invalid api key")`.
     NVIDIA NIM returns `401 Unauthorized` with payloads such as `{"detail": "Unauthorized"}` or `{"message": "Invalid Bearer token"}`. These do not match the hardcoded strings, causing the circuit breaker to count 3 failures before opening instead of tripping immediately on authentication rejection.
  3. **Real-time Pipeline 401 Trapping**:
     In `providers/llm_openai.py`, `probe()` cleanly traps 401s and outputs `"The server refused the key."` This is solid. However, in `complete()` and `chat()`, 401 raises `RuntimeError("401 ...")`. In `engine_pipeline.py`, this gracefully emits a speech failure line and terminates the turn, avoiding infinite retry storms.
- **Severity**: **HIGH** (See Mandate **M-5**).

---

### Criterion 5: Privacy Boundaries & Cloud Isolation
- **Rating**: **PASS**
- **Analysis**:
  - In `core/intelligence/router.py`:
    `is_private = request.is_private or (self.policy == RoutingPolicy.PRIVACY_STRICT)`
    `if is_private: candidates = [m for m in candidates if m.is_local]`
  - Setting `is_local = False` on `NvidiaNimProvider` and all its seeded models in `ModelMetadata` guarantees that `router.py` strictly excludes NVIDIA NIM when private context is evaluated.
  - In `providers/llm_openai.py`, setting `"local": False` in `CATALOG` ensures the UI properly labels it as a cloud endpoint (`☁`).
- **Severity**: None (Clean Pass).

---

### Criterion 6: Acceptance Testing Completeness
- **Rating**: **CONDITIONAL PASS**
- **Analysis**:
  - The Elephant spec outlines 11 test scenarios in Section 6, covering token usage, tool calling, streaming, 401 fast-fail, 429 backoff, 400 parameter sanitization, and fallback routing.
  - **Omission 1**: The test suite is isolated to `tests/test_nvidia_nim_provider.py` (Gateway only). There are no test declarations verifying the Conversation Pipeline integration in `providers/llm_openai.py` (`can_see` image probe, `PRESETS`, `CATALOG` discovery, `missing_fields` resolution).
  - **Omission 2**: No live test protocol is specified. Live integration tests must be gated with `@pytest.mark.skipif("NVIDIA_API_KEY" not in os.environ)` to allow deterministic offline CI while supporting live validation when keys are present.
- **Severity**: **MEDIUM** (See Mandate **M-7**).

---

## 3. Explicit Binding Architectural Mandates

The implementation phase **MUST NOT** proceed until the following binding mandates are formally integrated into the specification and codebase:

### **[M-1] Zero-Plaintext UI & Credential Bridge**
1. In `gui/providers_panel.py`, modify `_apply()` and `_remember_edits()`:
   When reading values for a provider with secret fields (`api_key`):
   - If a non-empty key is provided, invoke `core.security.credentials.store_credential(catalog_id, key)` immediately.
   - Replace the key in the in-memory `creds` dict with `""` or omit it before calling `save_providers()`, ensuring `api_keys.json` NEVER contains the plaintext secret.
2. In `providers/registry.missing_fields(provider_id, cfg)`:
   Update the key validation check:
   ```python
   # Check cfg first, then fall back to secure credential store and environment
   if catalog_entry(provider_id).get("local") is False:
       has_key = bool(str(cfg.get("api_key", "")).strip())
       if not has_key:
           base, entry = split_id(provider_id)
           from core.security.credentials import get_credential
           has_key = bool(get_credential(entry) or get_credential(base))
       if not has_key:
           out.append("API key")
   ```
3. In `gui/providers_panel.py`, when rendering an `api_key` field, if `cfg.get("api_key")` is empty, check `get_credential(entry)`. If present, display a masked placeholder (`••••••••`) to signal to the user that a secure OS-level credential is active.

---

### **[M-2] Credential Aliasing & Unified Resolution**
In `core/security/credentials.py`:
1. Update `ENV_FALLBACKS` to include:
   ```python
   "nvidia": ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"],
   "nvidia_nim": ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"],
   ```
2. In `store_credential` and `get_credential`, normalize alias lookups so querying either `"nvidia"` or `"nvidia_nim"` accesses the same Keyring/Vault entry.

---

### **[M-3] Gateway Provider Inheritance & DRY Compliance**
In `core/intelligence/providers/nvidia_nim.py`:
- `NvidiaNimProvider` **MUST** subclass `core.intelligence.providers.openai.OpenAIProvider`.
- Do not duplicate HTTP transport, SSE stream decoding, or message translation.
- Set default `base_url = "https://integrate.api.nvidia.com/v1"`, `is_local = False`, and `name = "nvidia_nim"`.
- Override `complete()` and `stream()` solely to inject NIM-specific parameter stripping on 400 errors and credential resolution.

---

### **[M-4] Deterministic Parameter Stripping on HTTP 400**
In `providers/llm_openai.py` and `core/intelligence/providers/nvidia_nim.py`:
1. Never send `frequency_penalty`, `presence_penalty`, `top_k`, or `logprobs` in default payload bodies to NIM endpoints.
2. If an HTTP 400 error occurs, parse the response body using regex:
   `re.search(r"(?:unrecognized|unknown|unsupported)\s+(?:request\s+)?argument\s+['\"]?([a-zA-Z0-9_]+)['\"]?", err_text, re.IGNORECASE)`
3. If an offending parameter name is identified and exists in the request body, delete that parameter from the payload and retry completion exactly once. If the retry fails or the error is not a parameter error, raise immediately.

---

### **[M-5] Exception Contract & Immediate Circuit Breaker Tripping on 401**
1. In `core/intelligence/types.py`, declare:
   ```python
   class AuthenticationError(ProviderError):
       """Raised when API key is missing, invalid, or rejected (HTTP 401)."""
   ```
2. In `core/intelligence/circuit_breaker.py`, update `record_failure()`:
   Ensure any error instance of `AuthenticationError`, or an error string containing `"401"`, `"unauthorized"`, or `"invalid bearer"`, immediately sets `self._state = CircuitState.OPEN` on the very first occurrence.

---

### **[M-6] Global Secret Redaction Logging Filter**
In `core/security/credentials.py`, implement `SecretRedactionFilter(logging.Filter)`:
```python
import re
_SECRET_PATTERNS = [
    re.compile(r"nvapi-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
]

class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pat in _SECRET_PATTERNS:
                record.msg = pat.sub("[REDACTED_API_KEY]", record.msg)
        return True
```
Attach this filter to root/OPTIMUS loggers during initialization.

---

### **[M-7] Complete Dual-Surface Test Coverage**
Split acceptance testing into two distinct test suites:
1. `tests/test_nvidia_nim_gateway.py`: Tests `NvidiaNimProvider`, capability matching, `router.py` privacy blocking (`PRIVACY_STRICT`), 401 immediate circuit breaking, 429 exponential backoff, and 400 parameter stripping retry.
2. `tests/test_nvidia_nim_pipeline.py`: Tests `providers/llm_openai.py` preset loading, catalog discovery, `missing_fields()` keyring fallback, `can_see()` image probe mock, and UI parameter masking.
3. Add live test suite decorated with:
   `@pytest.mark.skipif(not os.environ.get("NVIDIA_API_KEY"), reason="NVIDIA_API_KEY not set in environment")`

---

## 4. Audit Verdict & Sign-Off

- **Audit Status**: **CONDITIONAL PASS**
- **Action Required by Elephant Orchestrator**: Reconcile `docs/audits/nvidia-nim/elephant-investigation.md` to incorporate Mandates **M-1 through M-7**, then proceed with the dual-surface implementation.
