"""core/self_improvement/staging_generator.py — Isolated Staging Workspace Generator.

Implements:
- Strict workspace boundary confinement (rejecting path traversal, outside writes, and symlinks).
- SEC-01: Prohibits writing into core/, config/, .git/, or memory/ outside the assigned staging directory.
- SEC-03: Static AST code sanitizer banning dynamic imports, socket/network libraries, and execution escapes.
- SEC-08: Multi-file package integrity calculation (per-file SHA-256 and Merkle package integrity hash).
- SEC-12: Staging directory cleanup and retention lifecycle.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import shutil
import time
from pathlib import Path

from core.self_improvement.storage import SelfImprovementStore
from core.self_improvement.types import (
    GeneratedArtifact,
    ImprovementProposal,
    LifecycleState,
)

logger = logging.getLogger("optimus.self_improvement.staging_generator")

# Prohibited imports and AST attributes in generated self-improvement code
BANNED_MODULES = {
    "socket",
    "http",
    "urllib",
    "requests",
    "ftplib",
    "telnetlib",
    "importlib",
    "subprocess",
    "winreg",
    "ctypes",
    "posix",
    "nt",
}

BANNED_FUNCTIONS = {
    "eval",
    "exec",
    "__import__",
    "compile",
    "open",  # Raw open banned in pure declarative skills; tool-wrapped I/O preferred
    "system",
    "popen",
    "spawnl",
    "spawnv",
    "spawnle",
    "spawnve",
    "spawnlp",
    "spawnvp",
    "spawnlpe",
    "spawnvpe",
    "execl",
    "execv",
    "execle",
    "execve",
    "execlp",
    "execvp",
    "execlpe",
    "execvpe",
    "fork",
    "rmtree",
    "remove",
    "unlink",
    "rmdir",
}

BANNED_ATTRIBUTES = {
    "__subclasses__",
    "__globals__",
    "__builtins__",
    "__code__",
    "system",
    "popen",
}


class SecuritySanitizationError(Exception):
    """Raised when generated code contains prohibited security patterns."""


class StagingPathEscapeError(Exception):
    """Raised when an operation attempts to escape the staging boundary."""


class StagingCodeGenerator:
    """Generates, sanitizes, and packages self-improvement candidate code in staging isolation."""

    def __init__(self, staging_root: Path | str | None = None, store: SelfImprovementStore | None = None) -> None:
        if staging_root is None:
            staging_root = Path(__file__).resolve().parent.parent.parent / "memory" / "self_improvement" / "staging"
        self.staging_root = Path(staging_root)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.store = store or SelfImprovementStore.instance()

    def get_staging_dir(self, proposal_id: str) -> Path:
        """Return the dedicated, confined staging directory for a proposal."""
        staging_dir = (self.staging_root / proposal_id).resolve()
        # Verify strictly confined within staging_root
        if not str(staging_dir).startswith(str(self.staging_root.resolve())):
            raise StagingPathEscapeError(f"STAGING_ESCAPE: Directory '{staging_dir}' escapes staging root.")
        return staging_dir

    @staticmethod
    def sanitize_python_code(code_str: str) -> None:
        """SEC-03: Static AST code audit rejecting dynamic imports, sockets, and execution escapes."""
        if not code_str.strip():
            return

        try:
            tree = ast.parse(code_str)
        except SyntaxError as ex:
            raise SecuritySanitizationError(f"SYNTAX_ERROR: Generated code failed AST parsing: {ex}") from ex

        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    if root_pkg in BANNED_MODULES:
                        raise SecuritySanitizationError(f"BANNED_IMPORT: Module '{alias.name}' is prohibited in self-improvement code.")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    if root_pkg in BANNED_MODULES:
                        raise SecuritySanitizationError(f"BANNED_IMPORT: Module '{node.module}' is prohibited in self-improvement code.")

            # Check function calls (both direct Name and qualified Attribute)
            elif isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name in BANNED_FUNCTIONS:
                    raise SecuritySanitizationError(f"BANNED_CALL: Direct or qualified call to '{func_name}' is prohibited.")

            # Check sensitive attribute access
            elif isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRIBUTES:
                raise SecuritySanitizationError(f"BANNED_ATTRIBUTE: Access to '{node.attr}' is strictly forbidden.")

    def stage_artifact(
        self,
        proposal: ImprovementProposal,
        entrypoint_content: str,
        auxiliary_files: dict[str, str] | None = None,
        artifact_type: str = "SKILL_MD",
    ) -> GeneratedArtifact:
        """Create staged artifact files, sanitize content, and compute package checksums."""
        staging_dir = self.get_staging_dir(proposal.proposal_id)
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        # 1. Sanitize Python scripts if present
        if artifact_type == "TOOL_CODE" or entrypoint_content.startswith("#!/usr/bin/env python"):
            self.sanitize_python_code(entrypoint_content)

        if auxiliary_files:
            for rel_path, file_content in auxiliary_files.items():
                if rel_path.endswith(".py"):
                    self.sanitize_python_code(file_content)

        # 2. Write entrypoint file
        entrypoint_name = "SKILL.md" if artifact_type == "SKILL_MD" else "entrypoint.py"
        entrypoint_path = (staging_dir / entrypoint_name).resolve()

        # Enforce path containment
        if not str(entrypoint_path).startswith(str(staging_dir)):
            raise StagingPathEscapeError(f"PATH_TRAVERSAL: Entrypoint '{entrypoint_name}' escapes staging directory.")

        entrypoint_bytes = entrypoint_content.encode("utf-8")
        entrypoint_path.write_bytes(entrypoint_bytes)

        # 3. Write auxiliary files (SEC-08: multi-file support)
        files_checksums: dict[str, str] = {}
        files_checksums[entrypoint_name] = hashlib.sha256(entrypoint_bytes).hexdigest()

        if auxiliary_files:
            for rel_path, content in auxiliary_files.items():
                # Disallow symlinks, path traversal, or absolute paths
                clean_rel = rel_path.strip().replace("\\", "/")
                if clean_rel.startswith(("/", "\\")) or ".." in clean_rel or ":" in clean_rel:
                    raise StagingPathEscapeError(f"PATH_TRAVERSAL: Invalid relative path '{rel_path}' in auxiliary files.")

                dest_file = (staging_dir / clean_rel).resolve()
                if not str(dest_file).startswith(str(staging_dir)):
                    raise StagingPathEscapeError(f"STAGING_ESCAPE: Auxiliary file '{clean_rel}' escapes staging workspace.")

                dest_file.parent.mkdir(parents=True, exist_ok=True)
                content_bytes = content.encode("utf-8")
                dest_file.write_bytes(content_bytes)
                files_checksums[clean_rel] = hashlib.sha256(content_bytes).hexdigest()

        # 4. Compute Merkle root package integrity hash (SEC-08)
        sorted_keys = sorted(files_checksums.keys())
        merkle_payload = "|".join(f"{k}:{files_checksums[k]}" for k in sorted_keys)
        package_hash = hashlib.sha256(merkle_payload.encode("utf-8")).hexdigest()

        artifact_id = f"art_{proposal.proposal_id}_{time.strftime('%H%M%S')}"
        artifact = GeneratedArtifact(
            artifact_id=artifact_id,
            proposal_id=proposal.proposal_id,
            artifact_type=artifact_type,
            staging_path=str(staging_dir),
            content_sha256=files_checksums[entrypoint_name],
            files_checksums=files_checksums,
            package_integrity_hash=package_hash,
            generated_code=entrypoint_content,
            author_model="local_orchestrator",
            generated_at=time.time(),
            metadata={"file_count": len(files_checksums)},
        )

        proposal.transition_to(LifecycleState.ISOLATED)
        self.store.save_proposal(proposal)
        self.store.save_artifact(artifact)

        logger.info("Staged artifact '%s' at '%s' (package_hash=%s)", artifact_id, staging_dir, package_hash[:12])
        return artifact

    def cleanup_staging(self, proposal_id: str, force: bool = False) -> bool:
        """SEC-12: Clean up staging directory following promotion or rejection."""
        try:
            staging_dir = self.get_staging_dir(proposal_id)
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
                logger.info("Cleaned staging directory for '%s'.", proposal_id)
                return True
        except (OSError, ValueError) as ex:
            logger.warning("Failed cleaning staging directory for '%s': %s", proposal_id, ex)
        return False
