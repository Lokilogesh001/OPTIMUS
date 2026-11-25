# OPTIMUS Comprehensive Reliability Repair & Stable Release Specification
**Architecture & Implementation Specification (EGM Elephant Phase — Revised)**
**Status:** REVISED FOR INDEPENDENT GOLDFISH AUDIT  
**Date:** November 2026  
**Author:** Principal Systems Architect & Autonomous Systems Lead  
**Scope:** In-place reliability hardening, zero fabricated research, strict date/evidence validation, robust audio/GUI concurrency, clean cooperative shutdown, and stable release staging.

---

## 1. Executive Summary & Defect Inventory

This engineering specification establishes the end-to-end remediation plan for all 9 critical runtime defect areas identified in the OPTIMUS autonomous desktop assistant. In strict accordance with engineering policy, this is a corrective hardening mission without clean-room rewrites, code destruction, or unverified feature bloat.

### 1.1 Complete Defect Register

| Defect ID | Severity | Category | Affected Components | Root Cause | Proposed Remediation | Target Verification |
|---|---|---|---|---|---|---|
| **DEF-01** | CRITICAL | Code Hygiene / Scoping | `actions/web_search.py`, `core/planning/adaptive_executor.py` | `from core import outcome` placed at line 352 midway through module; unbound reference when exceptions occur earlier or when empty query is submitted; variable name shadowing in `adaptive_executor.py`. | Move import to module top; return `outcome.failed("web_search", ...)` on empty query; rename shadowed variable in executor to `exec_outcome`. | Unit tests in `tests/test_web_search_reliability.py` testing early errors and empty parameters. |
| **DEF-02** | CRITICAL | Search Grounding / Fact Integrity | `actions/web_search.py`, `core/sandbox/tools_research.py`, `core/tasks/tools.py`, `tests/test_advanced_missions.py` | Discarded publication dates in DDG news/search; missing timestamp validation (allowing future/impossible dates); fabricated research fallbacks in `tools_research.py` generating synthetic URLs (`https://reference.internal/...`). | Implement strict URL validation, ISO-8601 publication date parsing from metadata headers, dynamic wall-clock bounds checking (`timestamp <= time.time() + 86400s`), distinct publication vs event dates, `undated=True` labeling; purge synthetic citation generation in sandbox; update test fixtures with realistic mock search adapters. | Date validation tests, future-date rejection tests, zero-hallucination fallback assertion tests, and existing mission tests passing. |
| **DEF-03** | HIGH | Audio Pipeline / Device Hardware | `core/audio_stream.py`, `core/tts.py` | `_feeder_loop` raises unhandled errors or busy-spins on hardware disconnect (`_stream is None`); stereo audio passed to `resample_audio` before downmixing raises `ValueError`. | Downmix to mono *before* resampling in `AudioRingBuffer.write()`; resilient recovery loop in `_feeder_loop()` sleeping on `_has_data_event.wait(timeout=0.25)` when stream is lost; PortAudio stream reinitialization on device recovery; 1D linear resampling with zero DC offset. | Mock hardware disconnect tests, stereo downmix tests, sample-rate sweep (8k-48k), barge-in sub-20ms assertions. |
| **DEF-04** | HIGH | GUI Rendering / Event Loop | `gui/optimus/animation/qml/`, `gui/optimus/animation/animation_manager.py` | `onAudioLevelChanged` in `WaveformCanvas.qml` calls `canvas.requestPaint()` unconditionally; QQuickWidget rendering timers running while widgets/tabs are occluded or minimized, introducing frame drops in Qt event loop. | State-machine gated canvas rendering (`hasActiveDecay`); gate `onAudioLevelChanged` against silence and occlusion (`!root.paused && root.visible && (root.audioLevel > 0.005 || root.hasActiveDecay)`); active visibility change propagation across Control Center tabs; window deactivation pause. | Event-loop jitter measurement (< 15 ms P95); frame-drop audit during heavy background processing. |
| **DEF-05** | MEDIUM | IPC / Python-QML Bridge | `gui/optimus/animation/animation_bridge.py` | High-frequency telemetry and audio level updates from background threads causing excessive event queue backlog. | Lock-free thread-safe deadband thresholding (0.01 level delta); 30Hz coalescing loop on main thread; strictly queued signal dispatch. | Telemetry burst stress testing with 10,000 updates/sec without GUI freeze. |
| **DEF-06** | MEDIUM | Concurrency / Agent Contention | `main.py`, `gui/optimus/service_bridge.py`, `core/tasks/supervisor.py` | Unbounded polling rates for telemetry and background agents during voice synthesis and intensive UI animations. | Dynamic load shedding: reduce background polling frequencies when `animBridge.coreState == "SPEAKING"`; cache expensive telemetry calls; provide explicit `OptimusServiceBridge.set_throttled(True)`. | Resource consumption measurements (CPU < 5% idle, RAM drift < 1 KB/s). |
| **DEF-07** | HIGH | Process Lifecycle / Clean Shutdown | `main.py`, `gui/optimus/control_center.py`, `gui/optimus/service_bridge.py`, `core/audio_stream.py`, `gui/optimus/animation/resource_monitor.py` | Daemon thread leakage, unstopped timers in `OptimusServiceBridge`, or blocking join calls during application exit leading to process hangs or Python 3.13 teardown warnings. | Cooperative shutdown orchestration in `ControlCenter.closeEvent`, `OptimusServiceBridge.shutdown()`, and `MainWindow.closeEvent`; stop all timers; `ContinuousAudioStreamPlayer.reset_instance()`; bounded thread joins (<= 1.5s). | Repeated start/stop stress test with 0 orphan processes and 0 lingering threads. |
| **DEF-08** | HIGH | Startup Latency / App Registry | `core/application_intelligence/registry.py`, `gui/optimus/section_applications.py` | Synchronous inspection of 400+ Windows registry/filesystem entries in `list_all_apps()` / `refresh()` blocks GUI event loop on cold boot. | Decouple `list_all_apps()` and `list_running_apps()` from synchronous discovery: read strictly from in-memory cache; isolate Windows registry/filesystem scanning strictly to `_bg_scan`; UI populates table in batched chunks (64 rows per tick) with updates disabled. | Cold start time < 10.0s; 0 ms GUI thread blocking on application tab navigation. |
| **DEF-09** | CRITICAL | Verifiable Autonomous Execution | `core/agents/verifier.py`, `core/verification/engine.py`, `core/verification/verifiers.py`, `core/orchestrator/tools.py` | Subtasks completing on model ungrounded self-report without independent physical verification; `FilesystemVerifier` slurp memory hazard using `read_bytes()` on large deliverables. | Mandatory physical postcondition inference; inspect file size via `stat().st_size`; calculate SHA-256 via 64 KB chunk streaming; tamper-evident logging to `EvidenceLedger`. | End-to-end deliverable verification tests with negative test cases in `tests/test_verification_hardening.py` (empty file, missing file, incorrect sha256). |

---

## 2. Technical Architecture & Component Remediation

### 2.1 Web Search Reliability & Evidence Validation (DEF-01, DEF-02)

#### 2.1.1 Outcome Variable & Scoping Remediation
In `actions/web_search.py`:
- Place `from core import outcome` at the top of the module with standard library imports.
- Ensure all exit paths return typed `outcome` structures (`outcome.failed`, `outcome.done`, `outcome.untrusted`). Specifically, when parameters are missing or query is empty, return:
  ```python
  if not query and not items:
      return outcome.failed("web_search", "Search query or items required")
  ```
- In `core/planning/adaptive_executor.py`, rename the local variable `outcome` in `execute_plan` to `exec_outcome` to eliminate shadowing of `from core import outcome`.

#### 2.1.2 URL and Publication Date Validation Engine (`core/search_validator.py`)
Introduce a dedicated validator module: `core/search_validator.py` with the following architectural rules:
- **Source URL Validation:**
  - `validate_source_url(url: str) -> bool`: Validates scheme (`http`, `https`), valid FQDN / IP, non-local, non-loopback, RFC-compliant format. Rejects synthetic schemes or internal URLs unless explicitly authorized.
- **Publication Date vs Event Date Extraction:**
  - Publication dates MUST be extracted exclusively from structured metadata headers: `r.get("date")` from search APIs, HTTP `Last-Modified` headers, or standard HTML meta tags (`<meta property="article:published_time">`).
  - Text body snippets MUST NOT be regex-scanned for general dates as "publication dates", because snippets frequently mention historical or future event dates (e.g., "The 2028 Olympic games...").
  - If structured metadata is absent, the snippet is explicitly marked `publication_date=None, undated=True`.
- **Wall-Clock Bounds Checking & Normalization:**
  - `parse_and_validate_publication_date(date_raw: Any, current_timestamp: float | None = None) -> tuple[str | None, bool, str]`:
    - Normalizes ISO-8601 (`YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SSZ`), RFC-2822, or relative dates into UTC datetimes.
    - Compares against `current_timestamp or time.time()`.
    - Rejects future dates (`pub_date > now + 86400s`, allowing 24h clock skew).
    - Identifies invalid or impossible calendar dates (e.g., month > 12, day > 31).
    - Returns `(normalized_iso_date, is_valid, reason)`.
- **Search Evidence Formatting:**
  - `format_search_evidence(items: list[dict], current_time: float | None = None) -> list[dict]`:
    - Enriches each search snippet with `publication_date`, `verified_source=True/False`, and `undated=True/False`.
    - Flags undated snippets explicitly in display formatting (`[Undated / Unverified Publication Date]`) so the LLM and user never conflate undated articles with real-time news.

#### 2.1.3 Elimination of Fabricated Fallbacks & Test Harness Adaptation
In `core/sandbox/tools_research.py`:
- Purge lines 30–43 and 67–81 completely.
- Replace synthetic citations with structured failures:
  ```python
  if not raw_res or str(raw_res).startswith("status=failed"):
      # Allow test context mock override if explicitly provided in context
      if context and context.get("mock_search"):
          mock_data = context["mock_search"]
          return ToolResult(
              status="done",
              output=mock_data.get("output", f"Search results for: {query}"),
              evidence={"query": query, "source": "mock_fixture", "citations_count": len(mock_data.get("urls", []))},
          )
      return ToolResult(
          status="failed",
          error=f"No verified search results could be retrieved for query: '{query}'.",
          evidence={"query": query, "offline": True, "citations_count": 0}
      )
  ```
- In `tests/test_advanced_missions.py` (`temp_mission_env` fixture):
  - Configure the test sandbox coordinator context to supply realistic mock research fixtures (e.g. referencing `https://docs.python.org/3/whatsnew/` with verified dates) so that Test 3 (`test_mission_archetype_a_research_and_report`) verifies the research agent without relying on synthetic in-code fallbacks.

---

### 2.2 Audio Pipeline Reliability & Hardware Verification (DEF-03)

#### 2.2.1 Audio Resampling Stereo Downmixing & Fault Recovery
In `core/audio_stream.py`:
- **Mono Downmixing Before Resampling:**
  In `AudioRingBuffer.write(self, samples, orig_sample_rate)`:
  - Downmix multi-channel audio to mono *prior* to resampling:
    ```python
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if orig_sample_rate != self.sample_rate:
        samples = resample_audio(samples, orig_sample_rate, self.sample_rate)
    ```
- **Feeder Loop Hardware Disconnect & Throttling:**
  In `_feeder_loop(self)`:
  - When `stream.write()` raises `OSError` (e.g. `paOutputUnderflow` or device disconnected):
    - Close the dead stream cleanly, set `self._stream = None`.
    - Attempt graceful reinitialization via `self._init_stream()` up to 3 times with exponential backoff (250ms, 500ms, 1000ms).
    - If `self._stream is None`, do NOT busy-spin! Wait on `self._has_data_event.wait(timeout=0.25)` to prevent 100% CPU starvation while offline.
- **Continuous Resampling Stability:**
  - `resample_audio()` validates input dtype, bounds sample rate conversion between 8,000 Hz and 96,000 Hz, and preserves 0.0 DC offset via symmetric linear interpolation.

---

### 2.3 GUI & Animation Concurrency (DEF-04, DEF-05, DEF-08)

#### 2.3.1 QML Rendering Gating & Waveform Canvas Leak Elimination
In `gui/optimus/animation/qml/components/WaveformCanvas.qml`:
- Gate the `onAudioLevelChanged` connection handler:
  ```qml
  Connections {
      target: root
      function onAudioLevelChanged() {
          if (root.audioLevel > 0.005) {
              root.hasActiveDecay = true;
              root.decayFramesLeft = 10;
          }
          // Only request repaint if timer is not running, widget is visible/active, and there is active audio or decay
          if (!redrawTimer.running && !root.paused && root.visible && (root.audioLevel > 0.005 || root.hasActiveDecay)) {
              canvas.requestPaint();
          }
      }
  }
  ```
- Hook tab switches in `ControlCenter` and `QApplication.activeWindowChanged` to `AnimationManager.pause_all()` and `resume_all()`, ensuring occluded QQuickWidgets pause their scene graphs.

#### 2.3.2 Non-Blocking Application Registry (DEF-08)
In `core/application_intelligence/registry.py`:
- Decouple `list_all_apps()` and `list_running_apps()` from synchronous discovery:
  - `list_all_apps()` and `list_running_apps()` MUST read strictly from the in-memory `self._apps` cache. They never trigger `self.refresh()` synchronously.
  - Full system discovery (`discover_all_installed_applications()`) is strictly restricted to background execution (`_bg_scan` thread or explicit background task).
  - On startup, `ApplicationRegistry` reads `application_inventory.json` from disk in < 10 ms.
- In `gui/optimus/section_applications.py`:
  - `_refresh_apps_table()` populates rows in chunks of 64 using `QTimer.singleShot(0, ...)` with `tbl_apps.setUpdatesEnabled(False)` during insertion to prevent GUI thread lockups.

---

### 2.4 Agent Execution & Physical Deliverable Verification (DEF-09)

#### 2.4.1 Streaming SHA-256 Hashing & Strict Postcondition Verification
In `core/verification/verifiers.py` (`FilesystemVerifier`):
- Eliminate `target_path.read_bytes()` memory slurp hazard!
- Inspect file size directly via `target_path.stat().st_size`.
- Compute SHA-256 using 64 KB chunk streaming:
  ```python
  hasher = hashlib.sha256()
  with open(target_path, "rb") as f:
      while chunk := f.read(65536):
          hasher.update(chunk)
  calculated_sha256 = hasher.hexdigest()
  ```
- Any subtask specifying deliverables MUST verify:
  1. Target path exists.
  2. `st_size >= min_size` (default >= 1 byte; 0-byte files fail verification).
  3. Calculated SHA-256 matches expected SHA-256 (if specified).
- Log non-tamperable verification records to `EvidenceLedger`.

---

### 2.5 Load Shedding & Agent Contention (DEF-06)

#### 2.5.1 Voice Speech Priority Throttling
In `gui/optimus/service_bridge.py`:
- Add `set_throttled(throttled: bool)` to `OptimusServiceBridge`:
  - When `animBridge.coreState == "SPEAKING"`, bridge automatically calls `set_throttled(True)`.
  - In throttled mode:
    - Telemetry polling interval increases from 1.0s to 5.0s.
    - System monitor queries are served from `_top_proc_cache` without re-querying `psutil`.
    - Token flush timer is deferred.
  - When `coreState != "SPEAKING"`, normal polling resumes.

---

### 2.6 Cooperative Lifecycle & Clean Shutdown (DEF-07)

#### 2.6.1 Orderly Application Teardown Sequence
To prevent thread leaks, zombie background workers, and Python 3.13 teardown warnings:
- In `gui/optimus/service_bridge.py`:
  - Implement `shutdown(self)`:
    - Stop `_telemetry_timer` and `_token_flush_timer`.
    - Clear callbacks and unhook event bus listeners.
- In `core/audio_stream.py`:
  - `ContinuousAudioStreamPlayer.reset_instance()`:
    - Signals `_stop_event.set()`.
    - Wakes feeder loop via `_has_data_event.set()`.
    - Joins `_feeder_thread` with a strict `timeout=1.5s`.
    - Closes PortAudio stream and terminates PyAudio instance cleanly.
- In `gui/optimus/control_center.py` and `main.py`:
  - In `closeEvent(event)`:
    1. Stop animation manager: `AnimationManager.instance().shutdown()`.
    2. Shutdown service bridge: `service_bridge.shutdown()`.
    3. Shutdown audio player: `ContinuousAudioStreamPlayer.reset_instance()`.
    4. Shutdown adaptive performance monitor.
    5. Allow `super().closeEvent(event)`.

---

## 3. Stable Release Staging Architecture (DEF-00 / Stage 6)

In compliance with repository protection rules:
- Modify `Mark-LVII-main` in place. Preserve all existing working functionality, configuration, user data, staged and unstaged files.
- DO NOT reset, clean, force-push, or rewrite Git history.
- DO NOT commit or push without explicit user command.
- Create a designated stable release package directory: `release/stable_v1.0.0/` containing:
  - `run_optimus_stable.py`: Bootstraps `REPO_ROOT = Path(__file__).resolve().parent.parent` into `sys.path[0]` before importing application modules, guaranteeing reliable execution from any working directory.
  - `FEATURE_VERIFICATION_MATRIX.json`: Authoritative categorization of every subsystem (`VERIFIED`, `CONDITIONALLY VERIFIED`, `DISABLED`, `UNVERIFIED`).
  - `RELEASE_NOTES.md`: Summary of hardened subsystems and verification checksums.

---

## 4. Verification & Acceptance Testing Protocol

1. **Web Search Reliability Suite (`tests/test_web_search_reliability.py`):**
   - 10 unit tests covering: `outcome` scoping, empty parameters, URL validation, publication date extraction, future timestamp rejection, undated flag propagation, zero fabricated fallbacks, and circuit breaker trip.
2. **Negative Deliverable Verification Suite (`tests/test_verification_hardening.py`):**
   - 6 unit tests asserting proper failure records in `EvidenceLedger` for: missing files, 0-byte files, corrupt SHA-256, chunked 64 KB streaming calculation.
3. **Audio & Hardware Recovery Suite (`tests/realtime_runtime/`):**
   - Re-run all 26 real-time audio/runtime tests including stereo downmixing and feeder loop disconnect recovery.
4. **Animation & GUI Concurrency Suite (`tests/animation_engine/`):**
   - Re-run all 57 QML/animation tests, verifying non-leaking canvas repaints.
5. **Mission Verification Suite (`tests/test_advanced_missions.py`):**
   - Verify all 10 mission tests pass with real postcondition verification and mock search fixtures.
6. **Live Workload Profile:**
   - 30-minute stress recording: event loop latency, FPS, audio underruns, CPU/RAM telemetry.
