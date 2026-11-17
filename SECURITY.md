# Security Policy for OPTIMUS

## Supported Versions

| Version | Supported |
|---------|-----------|
| OPTIMUS (main branch) | :white_check_mark: |
| Legacy Mark-LVII | :warning: Community only |

---

## Reporting a Vulnerability

If you discover a security vulnerability or credential leak within OPTIMUS:

1. **Do not open a public GitHub issue.**
2. Report the vulnerability privately via GitHub Security Advisories or contact the maintainers directly.
3. Include:
   - Description of the vulnerability and attack vector.
   - Minimal reproducible proof-of-concept.
   - Affected modules (e.g., `core/sandbox/`, `core/intelligence/`, `core/interaction/`).
   - Potential impact on local system or host environment.

Maintainers will review the submission, patch the issue, and release a security advisory.

---

## Architectural Security Model

### 1. Secret Sanitization & Push Protection
- Secrets, tokens (`sk-...`, `AIza...`, `ghp_...`), and authorization headers are intercepted and masked by `core/interaction/sanitizer.py` before hitting logs or persistence files.
- `config/api_keys.json`, `.env`, and session databases are strictly excluded in `.gitignore`.

### 2. Strict Privacy Boundary Enforcement
- OPTIMUS implements policy-based privacy routing in `core/intelligence/router.py`.
- When `PRIVACY_STRICT` or a private request flag is active, outbound cloud routing (OpenAI, Anthropic, Gemini Cloud) is hard-blocked. All tasks route exclusively through local inference engines (Ollama, local vLLM).

### 3. Dynamic Tool Sandboxing & Permission Boundaries
- Tool executions are sandboxed and monitored via `core/sandbox/`.
- Irreversible or destructive operations (e.g. system commands, filesystem deletions) trigger human approval gates before execution proceeds.

### 4. Cryptographic Evidence Ledger
- Deliverables, file outputs, and verification results are committed to a tamper-evident SHA-256 evidence ledger (`core/verification/ledger.py`) to guarantee execution provenance.
