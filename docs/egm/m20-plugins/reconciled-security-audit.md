# OPTIMUS Plugin System (M20) — Reconciled Security Audit & Remediation Plan

**Author:** Principal Systems Architect & Security Engineer (Elephant)  
**Auditor:** Independent Adversarial Security & Architecture Critic (Goldfish)  
**Date:** 2026-11-22  
**Baseline Commit:** `2d32b07`  
**Target Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Branch:** `main`  
**Status:** **RECONCILED & APPROVED FOR REMEDIATION**

---

## 1. Executive Reconciliation Summary

The independent Goldfish security audit critically examined commit `2d32b07` and corroborated all 6 empirical exploit proofs from `tests/test_m20_security_audit_exploits.py`. 

The previous closure report incorrectly presented AST syntactic screening as an execution sandbox and claimed that `ThreadPoolExecutor` timeouts terminated execution. In reality:
1. Python threads cannot be asynchronously terminated in-process. `with ThreadPoolExecutor` hangs upon exit when a worker thread blocks or loops indefinitely.
2. AST screening only inspected top-level expressions and left class bodies, decorators, comprehensions, and `_DANGEROUS_MODULES` imports unenforced.
3. PolicyEngine evaluation failed open upon internal exceptions.
4. Output sanitization was omitted on successful return paths, and Google Gemini API keys (`AIzaSy...`) were missing from redaction regexes.

All findings are accepted without reservation. The following table establishes the authoritative remediation strategy.

---

## 2. Reconciled Vulnerability Resolution Matrix

| Vulnerability ID | Severity | Root Cause | Authoritative Remediation Mandate |
|:---|:---:|:---|:---|
| **VULN-01** | **Critical** | AST Sandbox Illusion & Bypass | Deep AST inspection via `ast.walk`: inspect all `ast.Import` and `ast.ImportFrom` against `_DANGEROUS_MODULES` (strictly forbidden for non-execution tiers); restrict `ast.ClassDef` bodies to functions and simple constant assignments; forbid arbitrary call nodes in `decorator_list`. Formally clarify in documentation that AST screening is a syntactic safety gate, NOT an OS security boundary. |
| **VULN-02** | **Critical** | Fail-Open PolicyEngine Catch | In `PluginRegistry.run()`, change `except Exception as pe_ex:` to fail closed: return `f"SECURITY_ERROR: Authorization evaluation failed for '{name}': {pe_ex}"`. Zero execution permitted without positive authorization. |
| **VULN-03** | **High** | ThreadPoolExecutor Context Hang | Eliminate `with ThreadPoolExecutor(max_workers=1) as executor:` which calls `shutdown(wait=True)`. Explicitly call `executor.shutdown(wait=False, cancel_futures=True)` on `TimeoutError` and normal exit, unblocking the caller thread immediately. |
| **VULN-04** | **High** | Unredacted Secrets on Success | In `PluginRegistry.run()`, route normal return values through `self.sanitize_secrets(str(result or "Done."))` before delivering to caller. |
| **VULN-05** | **Medium** | Missing Gemini API Key Redaction | Add `(re.compile(r"AIza[0-9A-Za-z-_]{35}", re.IGNORECASE), "[REDACTED_GEMINI_KEY]")` to `_SECRET_PATTERNS` in `core/orchestrator/tools.py`. |
| **VULN-06** | **Medium** | In-Process Memory Boundary Limit | Explicitly document in architecture specs that in-process plugins share memory with the core application. Recommend OS-level process isolation (subprocess + Job Objects) for untrusted third-party plugins. |
| **VULN-07** | **Medium** | Stale ToolRegistry & UI Toggle | In `gui/overlays.py:_toggle`, retrieve plugin capability tier from registry before toggling enabled state. In `save_plugin_enabled`, update ToolRegistry definition availability. |

---

## 3. Detailed Architectural Fix Specifications

### 3.1 Hardened AST Screening (`core/plugin_loader.py`)
```python
class PluginASTValidator:
    @classmethod
    def validate_source(cls, path: Path) -> tuple[bool, str]:
        # 1. Parse AST
        # 2. Walk entire tree:
        #    - Forbid any Import/ImportFrom of _DANGEROUS_MODULES
        #    - Forbid calls in decorator_list
        #    - Inside ClassDef: forbid Call nodes or non-def statements
        # 3. Forbid top-level Expr(Call) and expressions with dynamic calls
```

### 3.2 Un-joined Executor Shutdown (`core/plugin_loader.py`)
```python
executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
try:
    future = executor.submit(_call_run, rec.run, parameters or {}, player, session_memory)
    result = future.result(timeout=timeout_sec)
    executor.shutdown(wait=False)
    return self.sanitize_secrets(str(result or "Done."))
except concurrent.futures.TimeoutError:
    executor.shutdown(wait=False, cancel_futures=True)
    msg = f"Sir, the '{name}' plugin timed out after {timeout_sec:.0f} seconds."
    self._logger(msg)
    self._notify(msg)
    return msg
```

### 3.3 Fail-Closed PolicyEngine Evaluation
```python
except Exception as pe_ex:
    err_msg = f"SECURITY_ERROR: Authorization evaluation failed for '{name}': {pe_ex}"
    self._logger(err_msg)
    return err_msg
```
