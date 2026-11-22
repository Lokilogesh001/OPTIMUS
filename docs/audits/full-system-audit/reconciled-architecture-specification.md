# OPTIMUS Full-System Engineering Audit — Reconciled Architecture Specification

**Milestone:** Full-System Engineering Audit & Remediation  
**Date:** 2026-11-23  
**Auditor Verdict:** CONDITIONAL PASS (Incorporating All Goldfish Amendments)

---

## 1. Reconciliation Matrix

| Goldfish Finding ID | Severity | Proposed Elephant Remedy | Goldfish Feedback / Blind Spot | Reconciled Implementation Specification |
|---|---|---|---|---|
| **REC-GUI-01** | **P0 (Blocker)** | Add `text: str \| None = None` to `RingGauge.set_value`. | `float('nan')` or `float('inf')` from broken drivers causes `ValueError: cannot convert float NaN to integer`. | Implement robust numerical sanitization using `math.isnan` and `math.isinf`. Default to `v = 0.0` and `text = "--"` when non-finite values are received. |
| **REC-GUI-02** | **P1 (Major)** | Direct child widgets to `panel.body`. | Overwriting margins with small top margin clobbers HoloPanel header (HEADER is 30px). | When configuring `panel.body.setContentsMargins`, enforce `top >= 32px` (`8, 32, 8, 8`). |
| **REC-GUI-03** | **P1 (Major)** | Wrap naked QSS properties into type selector blocks. | Verified valid; 5 files had naked CSS concatenated with `scroll_css()`. | Enclose all styles in `QScrollArea { ... }`, `QListWidget { ... }`, `QTableWidget { ... }` before concatenating `scroll_css()`. |
| **REC-SEC-01** | **P1 (Major)** | Fix Google key regex range in `tools.py`. | Google keys have 30–45 chars; also add Anthropic and AWS access key patterns. | Update regex to `r"AIza[0-9A-Za-z_\-]{30,45}"`, add `r"sk-ant-[a-zA-Z0-9_\-]{20,}"` and `r"AKIA[0-9A-Z]{16}"`. |
| **REC-SEC-02** | **P2 (Quality)** | Update test assertion in `test_workspace_security_audit.py`. | Assert exact PolicyEngine output and verify `approval_challenge` canonical hash. | Align test assertions with `MANDATORY_APPROVAL_REQUIRED` and assert presence of `canonical_hash`. |
| **REC-PERF-01** | **P0 (Blocker)** | Use `threading.RLock()` for `WindowsSystemMonitorAdapter._lock`. | Confirmed necessary to prevent startup deadlocks. | Lock is reentrant `threading.RLock()`. |
| **REC-SYS-01** | **P1 (Major)** | Exponential backoff for LibreHardwareMonitor WMI. | Ensure COM apartment clean uninitialization without leaking references. | Use `pythoncom.CoInitialize()` with `finally: pythoncom.CoUninitialize()` and exponential backoff (`60s -> 120s -> 300s -> 600s max`). |

---

## 2. Detailed Technical Amendments

### 1. `gui/widgets.py` — `RingGauge.set_value`
```python
def set_value(self, pct: float, text: str | None = None) -> None:
    try:
        v_val = float(pct)
        if math.isnan(v_val) or math.isinf(v_val):
            v = 0.0
            is_valid = False
        else:
            v = max(0.0, min(100.0, v_val))
            is_valid = True
    except (TypeError, ValueError):
        v = 0.0
        is_valid = False

    if text is None:
        text = f"{int(round(v))}%" if is_valid else "--"

    if v == self._v and text == self._text:
        return
    self._v, self._text = v, text
    self.update()
```

### 2. `gui/optimus/section_missions.py` & `gui/optimus/section_proactive.py`
In `section_missions.py`:
- Replace `left_layout = QVBoxLayout(left_panel)` with `left_layout = left_panel.body` and set margins `left_layout.setContentsMargins(8, 34, 8, 8)`.
- Replace `right_layout = QVBoxLayout(right_panel)` with `right_layout = right_panel.body` and set margins `right_layout.setContentsMargins(10, 34, 10, 10)`.

In `section_proactive.py`:
- Replace `ll = QVBoxLayout(left_panel)` with `ll = left_panel.body` and margins `(8, 34, 8, 8)`.
- Replace `rl = QVBoxLayout(right_panel)` with `rl = right_panel.body` and margins `(8, 34, 8, 8)`.
- Replace `pl = QVBoxLayout(panel)` with `pl = panel.body` and margins `(12, 34, 12, 12)`.

### 3. QSS Stylesheet Normalization Across `gui/optimus/`
Wrap all naked CSS rules in proper widget type selectors before appending `scroll_css()`:
- `gui/optimus/context_panel.py`: `scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }" + scroll_css())`
- `gui/optimus/dashboard.py`: Wrap `self.wf_dag_scroll` and `self.agents_scroll` in `QScrollArea { ... }`.
- `gui/optimus/nav_bar.py`: `scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }" + scroll_css())`
- `gui/optimus/section_developer.py`: Wrap `file_list` in `QListWidget { ... }` and `mod_table` in `QTableWidget { ... }`.
- `gui/optimus/section_self_improvement.py`: `scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }" + scroll_css())`
- `gui/optimus/section_workflows.py`: Wrap `self.dag_scroll` in `QScrollArea { ... }`.
- `gui/optimus/section_vision.py`: Separate table scrollbar styling properly.

### 4. `core/orchestrator/tools.py` — Credential Redaction Patterns
```python
_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,45}", re.IGNORECASE), "[REDACTED_GEMINI_KEY]"),
    (re.compile(r"sk-[a-zA-Z0-9_\-]{20,}", re.IGNORECASE), "[REDACTED_API_KEY]"),
    (re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}", re.IGNORECASE), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE), "[REDACTED_AWS_KEY]"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36,}", re.IGNORECASE), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(Bearer\s+)[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"(['\"]?(?:password|passwd|secret|api_?key)['\"]?\s*[:=]\s*['\"])[^'\"]+(['\"])", re.IGNORECASE), r"\1[REDACTED]\2"),
]
```

### 5. `tests/test_workspace_security_audit.py`
Update `test_mutating_tool_blocked_without_approval_token` and `test_secret_redaction_in_tool_output` to match reconciled patterns.
