# Milestone 15.1: Native Windows Explorer Launch Capability — Implementation Report

**Status:** Complete  
**Date:** 2026-11-22  
**Target Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Branch:** `main`  
**Capability:** `open_in_explorer(path, select_file=False)`

---

## 1. Executive Summary

Milestone 15.1 bridges the gap between OPTIMUS's internal filesystem intelligence and the native Windows Desktop experience. Previously, commands such as `"Open D:\project in Explorer"` or `"Show this directory in Explorer"` either listed folder contents in text or resulted in OPTIMUS claiming it could not interact with Windows Explorer.

Milestone 15.1 introduces a first-class, secure capability `open_in_explorer` that opens directories or highlights files in Windows File Explorer via native Win32 process execution, backed by path canonicalization, capability-based security checks, and Session 0 isolation prevention.

---

## 2. Key Architecture & Components Implemented

### 2.1 UnifiedFilesystemService (`core/filesystem/service.py`)
- Added `open_in_explorer(path: str | Path, select_file: bool = False, context: dict | None = None) -> ToolResult`.
- Canonicalizes path via `resolve_path()` (enforcing UNC blocking, device namespace blocking, and path normalization).
- Enforces access control via `check_access(target, "open_in_explorer")`.
- Detects Windows Session 0 isolation via `ProcessIdToSessionId` and `WTSGetActiveConsoleSessionId`.
- Safely spawns `explorer.exe` using `subprocess.Popen([explorer_exe, ...], shell=False)`.
- If target is a file or `select_file=True`, passes `/select,"<target>"` to highlight the item.
- Fallback support for macOS (`open`) and Linux (`xdg-open`).

### 2.2 Tool Registry & Agent Profiles (`core/orchestrator/tools.py`, `core/orchestrator/agents.py`, `core/agents/`)
- Registered `open_in_explorer` under `CapabilityTier.READ_ONLY` (`is_mutating=False`).
- Registered aliases: `open_directory` and `open_folder`.
- Granted tool permissions across agent profiles: `filesystem`, `general`, `developer`, `researcher`, and `execution_agent`.

### 2.3 Intent Classification & Orchestrator Fast-Path (`core/orchestrator/mre.py`, `core/orchestrator/orchestrator.py`)
- Extended `MREClassifier._parse_explorer_intent()` to recognize natural variations:
  - `"Open <path> in Explorer"`
  - `"Show this directory in Explorer"`
  - `"Open this folder"`
  - `"Show this file in Explorer"`
  - `"Open downloads in explorer"`
- Resolves contextual semantic tokens (`"this"`, `"here"`, `"workspace"`, `"."`) to current working directory.
- `CentralOrchestrator._try_fast_path` directly dispatches `open_in_explorer` without wasting LLM reasoning cycles.

### 2.4 Action Controller (`actions/file_controller.py`)
- Added `open_in_explorer(path, select_file=False)`.
- Mapped actions `"open"`, `"open_folder"`, `"open_directory"`, `"open_in_explorer"`, `"explore"` to call `open_in_explorer`.

### 2.5 Windows Runtime Subsystem (`core/windows_runtime/`)
- Integrated `OperationType.OPEN_IN_EXPLORER` in `broker.py`, `client.py`, and `tools.py`.
- Classed under routine operations in `policy.py` for frictionless local development.
