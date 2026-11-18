"""core/developer/workspace.py — Authorized Development Workspace for OPTIMUS.

Enforces:
- Workspace boundary confinement (prevents directory traversal and symlink escapes).
- File snapshot backups before modification and one-click rollback.
- Unified diff computation for file modifications.
- AST symbol indexing across workspace source code.
- Fast regex and text grep search within authorized files.
- Modified files history and session tracking.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".vscode",
    ".idea",
    ".optimus",
}

IGNORE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".dll",
    ".so",
    ".exe",
    ".bin",
    ".iso",
    ".tar",
    ".gz",
    ".zip",
    ".7z",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".db",
    ".sqlite",
    ".sqlite3",
}


@dataclass
class FileSnapshot:
    """Snapshot record of a workspace file prior to modification."""
    snapshot_id: str
    relative_path: str
    backup_path: str
    original_sha256: str
    timestamp: float
    size_bytes: int


@dataclass
class WorkspaceModification:
    """Record of a file modification applied during development."""
    relative_path: str
    action: str  # "create", "modify", "delete"
    timestamp: float
    new_sha256: str
    diff: str = ""
    snapshot_id: str | None = None


class DevelopmentWorkspace:
    """Dedicated Development Workspace controller with path confinement and rollback."""

    def __init__(self, root_dir: Path | str | None = None) -> None:
        if root_dir is None:
            # Default to the authoritative project root
            root_dir = Path(__file__).resolve().parent.parent.parent
        self.root = Path(root_dir).resolve()
        if not self.root.exists() or not self.root.is_dir():
            self.root.mkdir(parents=True, exist_ok=True)

        self.backup_dir = self.root / ".optimus" / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self._snapshots: dict[str, list[FileSnapshot]] = {}  # rel_path -> list of snapshots
        self._modifications: list[WorkspaceModification] = []

    def set_root(self, new_root: Path | str) -> bool:
        """Switch authorized development workspace to a new directory."""
        p = Path(new_root).resolve()
        if not p.is_dir():
            return False
        self.root = p
        self.backup_dir = self.root / ".optimus" / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._snapshots.clear()
        self._modifications.clear()
        return True

    # ── Security & Boundary Confinement ──────────────────────────────────────

    def is_path_safe(self, path: Path | str) -> bool:
        """Verify whether a target path is strictly confined within the workspace."""
        try:
            target = self.resolve_path(path)
            return target == self.root or self.root in target.parents
        except Exception:
            return False

    def resolve_path(self, relative_or_absolute: Path | str, allow_cross_drive: bool = False) -> Path:
        """Resolve a path safely, preventing traversal and symlink escapes."""
        raw = Path(relative_or_absolute)
        if raw.is_absolute():
            resolved = raw.resolve()
        else:
            resolved = (self.root / raw).resolve()

        # Boundary check
        if resolved != self.root and self.root not in resolved.parents:
            if allow_cross_drive:
                try:
                    from core.windows_runtime.policy import WindowsRuntimePolicy
                    if resolved.is_absolute() and resolved.drive:
                        mounted = WindowsRuntimePolicy.discover_mounted_drives()
                        drive_prefix = resolved.drive.upper()
                        if any(m.upper().startswith(drive_prefix) for m in mounted):
                            return resolved
                except Exception:  # noqa: BLE001, S110
                    pass
            raise PermissionError(
                f"WORKSPACE_VIOLATION: Path '{resolved}' escapes authorized workspace root '{self.root}'"
            )
        return resolved

    def to_relative(self, path: Path | str) -> str:
        """Convert a path into a normalized workspace-relative string."""
        resolved = self.resolve_path(path)
        return str(resolved.relative_to(self.root)).replace("\\", "/")

    # ── File Operations & Rollback ───────────────────────────────────────────

    def read_file(self, relative_path: str, max_chars: int = 50000) -> tuple[str, str, int]:
        """Read a workspace file. Returns (content, sha256_digest, byte_count)."""
        target = self.resolve_path(relative_path)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: '{relative_path}'")

        raw_bytes = target.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        text = raw_bytes.decode("utf-8", errors="replace")
        return text[:max_chars], digest, len(raw_bytes)

    def write_file(
        self,
        relative_path: str,
        content: str,
        create_backup: bool = True,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Write content to a file in workspace, creating automatic backup snapshot."""
        target = self.resolve_path(relative_path)
        norm_rel = self.to_relative(target)

        # Disallow writing into .optimus internal directory
        if norm_rel.startswith(".optimus/"):
            return False, "Cannot write to internal .optimus workspace directory.", {}

        snapshot_id: str | None = None
        old_content = ""
        action = "create"

        if target.exists() and target.is_file():
            action = "modify"
            old_bytes = target.read_bytes()
            old_content = old_bytes.decode("utf-8", errors="replace")
            old_digest = hashlib.sha256(old_bytes).hexdigest()

            if create_backup:
                ts = time.time()
                snap_id = f"snap_{int(ts)}_{old_digest[:8]}"
                safe_name = norm_rel.replace("/", "_").replace("\\", "_")
                bak_filename = f"{safe_name}_{snap_id}.bak"
                bak_path = self.backup_dir / bak_filename
                bak_path.write_bytes(old_bytes)

                snapshot = FileSnapshot(
                    snapshot_id=snap_id,
                    relative_path=norm_rel,
                    backup_path=str(bak_path),
                    original_sha256=old_digest,
                    timestamp=ts,
                    size_bytes=len(old_bytes),
                )
                self._snapshots.setdefault(norm_rel, []).append(snapshot)
                snapshot_id = snap_id

        # Write new content
        target.parent.mkdir(parents=True, exist_ok=True)
        new_bytes = content.encode("utf-8")
        target.write_bytes(new_bytes)
        new_digest = hashlib.sha256(new_bytes).hexdigest()

        # Compute unified diff
        diff_lines = list(difflib.unified_diff(
            old_content.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{norm_rel}",
            tofile=f"b/{norm_rel}",
        ))
        diff_str = "".join(diff_lines)

        mod = WorkspaceModification(
            relative_path=norm_rel,
            action=action,
            timestamp=time.time(),
            new_sha256=new_digest,
            diff=diff_str,
            snapshot_id=snapshot_id,
        )
        self._modifications.append(mod)

        evidence = {
            "relative_path": norm_rel,
            "absolute_path": str(target),
            "action": action,
            "sha256": new_digest,
            "bytes_written": len(new_bytes),
            "snapshot_id": snapshot_id,
            "diff_summary": f"+{sum(1 for l in diff_lines if l.startswith('+') and not l.startswith('+++'))} / -{sum(1 for l in diff_lines if l.startswith('-') and not l.startswith('---'))} lines",
        }
        return True, f"File '{norm_rel}' successfully written ({len(new_bytes)} bytes).", evidence

    def rollback(self, relative_path: str, snapshot_id: str | None = None) -> tuple[bool, str]:
        """Rollback a modified file to its prior snapshot."""
        target = self.resolve_path(relative_path)
        norm_rel = self.to_relative(target)

        snaps = self._snapshots.get(norm_rel, [])
        if not snaps:
            return False, f"No backup snapshots found for '{norm_rel}'."

        if snapshot_id:
            chosen = next((s for s in snaps if s.snapshot_id == snapshot_id), None)
            if not chosen:
                return False, f"Snapshot ID '{snapshot_id}' not found for '{norm_rel}'."
        else:
            chosen = snaps[-1]

        bak_file = Path(chosen.backup_path)
        if not bak_file.is_file():
            return False, f"Backup file on disk '{bak_file}' is missing."

        # Restore original bytes
        target.write_bytes(bak_file.read_bytes())
        return True, f"Successfully rolled back '{norm_rel}' to snapshot '{chosen.snapshot_id}'."

    def get_diff(self, relative_path: str) -> str:
        """Retrieve unified diff for the most recent modification of a file."""
        norm_rel = self.to_relative(self.resolve_path(relative_path))
        mods = [m for m in self._modifications if m.relative_path == norm_rel]
        if mods and mods[-1].diff:
            return mods[-1].diff

        # If no active in-session modification diff, diff against latest snapshot if available
        snaps = self._snapshots.get(norm_rel, [])
        if snaps and Path(snaps[-1].backup_path).is_file():
            old_text = Path(snaps[-1].backup_path).read_text(encoding="utf-8", errors="replace")
            current_target = self.resolve_path(relative_path)
            current_text = current_target.read_text(encoding="utf-8", errors="replace") if current_target.exists() else ""
            return "".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                current_text.splitlines(keepends=True),
                fromfile=f"a/{norm_rel}",
                tofile=f"b/{norm_rel}",
            ))
        return "No diff available."

    def list_modified_files(self) -> list[dict[str, Any]]:
        """Return list of modified files recorded during current session."""
        results = []
        for m in self._modifications:
            results.append({
                "relative_path": m.relative_path,
                "action": m.action,
                "timestamp": m.timestamp,
                "time_str": time.strftime("%H:%M:%S", time.localtime(m.timestamp)),
                "sha256": m.new_sha256[:8],
                "snapshot_id": m.snapshot_id,
            })
        return results

    # ── Code Intelligence & Symbol Search ────────────────────────────────────

    def search_symbols(self, query: str = "", max_results: int = 50) -> list[dict[str, Any]]:
        """Search functions, classes, and methods across workspace Python files using AST."""
        query_clean = query.strip().lower()
        results: list[dict[str, Any]] = []

        for root, dirs, files in os.walk(self.root):
            # Prune ignored directories
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

            for fname in files:
                if not fname.endswith(".py"):
                    continue
                file_path = Path(root) / fname
                try:
                    code_text = file_path.read_text(encoding="utf-8", errors="replace")
                    tree = ast.parse(code_text, filename=fname)
                    rel_path = self.to_relative(file_path)

                    for node in ast.walk(tree):
                        if isinstance(node, ast.ClassDef):
                            name = node.name
                            if not query_clean or query_clean in name.lower():
                                results.append({
                                    "kind": "class",
                                    "name": name,
                                    "file": rel_path,
                                    "line": node.lineno,
                                    "docstring": (ast.get_docstring(node) or "")[:80],
                                })
                        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            name = node.name
                            if not query_clean or query_clean in name.lower():
                                args = [a.arg for a in node.args.args]
                                results.append({
                                    "kind": "function" if not isinstance(node, ast.AsyncFunctionDef) else "async_function",
                                    "name": name,
                                    "file": rel_path,
                                    "line": node.lineno,
                                    "arguments": args,
                                    "docstring": (ast.get_docstring(node) or "")[:80],
                                })
                        if len(results) >= max_results:
                            return results
                except Exception:
                    continue

        return results

    def grep_search(
        self,
        query: str,
        is_regex: bool = False,
        file_pattern: str = "*",
        max_matches: int = 100,
    ) -> list[dict[str, Any]]:
        """Search text or regular expressions across workspace files."""
        if not query:
            return []

        pattern: re.Pattern | None = None
        if is_regex:
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error:
                return []

        matches: list[dict[str, Any]] = []
        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in IGNORE_EXTENSIONS:
                    continue

                file_path = Path(root) / fname
                rel_path = self.to_relative(file_path)

                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, start=1):
                            line_stripped = line.rstrip("\r\n")
                            matched = False
                            if pattern:
                                if pattern.search(line_stripped):
                                    matched = True
                            elif query.lower() in line_stripped.lower():
                                matched = True

                            if matched:
                                matches.append({
                                    "file": rel_path,
                                    "line": lineno,
                                    "content": line_stripped[:160],
                                })
                                if len(matches) >= max_matches:
                                    return matches
                except Exception:
                    continue

        return matches

    # ── Workspace Tree Exploration & Statistics ──────────────────────────────

    def get_tree(self, max_depth: int = 3, max_entries: int = 250) -> list[dict[str, Any]]:
        """Return hierarchical file and folder structure for workspace explorer."""
        entries: list[dict[str, Any]] = []

        def _traverse(current: Path, depth: int):
            if depth > max_depth or len(entries) >= max_entries:
                return

            try:
                items = sorted(list(current.iterdir()), key=lambda x: (not x.is_dir(), x.name.lower()))
            except Exception:
                return

            for item in items:
                name = item.name
                if name in IGNORE_DIRS or (name.startswith(".") and name != ".env.example"):
                    continue

                rel = self.to_relative(item)
                if item.is_dir():
                    entries.append({
                        "name": name,
                        "path": rel,
                        "is_dir": True,
                        "depth": depth,
                    })
                    _traverse(item, depth + 1)
                elif item.is_file():
                    ext = item.suffix.lower()
                    if ext in IGNORE_EXTENSIONS:
                        continue
                    size = item.stat().st_size
                    entries.append({
                        "name": name,
                        "path": rel,
                        "is_dir": False,
                        "size": size,
                        "depth": depth,
                    })
                if len(entries) >= max_entries:
                    break

        _traverse(self.root, 0)
        return entries

    def get_stats(self) -> dict[str, Any]:
        """Compute summary statistics for the active workspace."""
        total_files = 0
        py_files = 0
        total_bytes = 0

        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in IGNORE_EXTENSIONS:
                    continue
                total_files += 1
                if ext == ".py":
                    py_files += 1
                try:
                    total_bytes += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass

        return {
            "root": str(self.root),
            "name": self.root.name,
            "total_files": total_files,
            "python_files": py_files,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "modified_in_session": len(self._modifications),
        }
