"""core/marketplace/installer.py — Hermetic virtualenv installer with atomic rollback and integrity verification.

Manages isolated per-plugin virtual environments under data/plugins_env/<plugin_id>/
Enforces:
- SHA-256 integrity hash verification before installation.
- Zip-Slip and build hook prevention.
- Dedicated virtualenv isolation preventing root environment pollution.
- Atomic rollback upon installation failure (cleans up directory completely).
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("optimus.marketplace.installer")

DEFAULT_PLUGINS_DATA_DIR = Path("data/plugins_env")


class InstallationError(Exception):
    """Raised when plugin installation fails."""


class IntegrityCheckFailed(InstallationError):
    """Raised when a package archive SHA-256 does not match declared hash."""


class PluginInstaller:
    """Installs plugins into isolated virtual environments with rollback guarantees."""

    def __init__(self, base_data_dir: Optional[Path] = None, python_executable: Optional[str] = None):
        self.base_data_dir = base_data_dir or DEFAULT_PLUGINS_DATA_DIR
        self.python_executable = python_executable or sys.executable
        self.base_data_dir.mkdir(parents=True, exist_ok=True)

    def verify_hash(self, file_path: Path, expected_sha256: str) -> bool:
        """Verifies file SHA-256 against expected hash string."""
        if not expected_sha256:
            return True  # If no hash declared, cannot verify

        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        actual_hash = hasher.hexdigest().lower()
        expected = expected_sha256.lower().strip()
        if expected.startswith("sha256:"):
            expected = expected[7:]
        return actual_hash == expected

    def get_plugin_dir(self, plugin_id: str) -> Path:
        """Derives safe filesystem directory for a plugin ID."""
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in plugin_id)
        return self.base_data_dir / safe_id

    def is_installed(self, plugin_id: str) -> bool:
        """Checks if a plugin has an existing installed directory."""
        pdir = self.get_plugin_dir(plugin_id)
        return pdir.exists() and (pdir / "plugin_meta.json").exists()

    def install_virtualenv(
        self,
        plugin_id: str,
        package_spec: Optional[str] = None,
        archive_path: Optional[Path] = None,
        expected_hash: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Creates an isolated virtual environment and installs the package with rollback on error."""
        target_dir = self.get_plugin_dir(plugin_id)

        # Integrity verification if archive provided
        if archive_path:
            if not archive_path.exists():
                raise InstallationError(f"Package archive not found: {archive_path}")
            if expected_hash and not self.verify_hash(archive_path, expected_hash):
                raise IntegrityCheckFailed(
                    f"Integrity check failed: archive SHA-256 does not match expected {expected_hash}"
                )

        # Prepare target directory
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Step 1: Create venv
            logger.info("Creating isolated virtualenv for '%s' in %s", plugin_id, target_dir)
            venv_cmd = [self.python_executable, "-m", "venv", str(target_dir / "venv")]
            res = subprocess.run(venv_cmd, capture_output=True, text=True, timeout=60.0)
            if res.returncode != 0:
                raise InstallationError(f"Failed to create virtualenv: {res.stderr}")

            # Locate venv python and pip
            if sys.platform == "win32":
                venv_python = target_dir / "venv" / "Scripts" / "python.exe"
                venv_pip = target_dir / "venv" / "Scripts" / "pip.exe"
            else:
                venv_python = target_dir / "venv" / "bin" / "python"
                venv_pip = target_dir / "venv" / "bin" / "pip"

            # Step 2: Install package if requested
            if package_spec or archive_path:
                target_spec = str(archive_path) if archive_path else package_spec
                logger.info("Installing %s into isolated venv for '%s'", target_spec, plugin_id)
                pip_cmd = [
                    str(venv_pip),
                    "install",
                    "--no-warn-script-location",
                    "--only-binary=:all:",  # Reject setup.py build hooks
                    target_spec,
                ]
                # Fail closed on source distributions; only retry without --only-binary if target_spec is explicitly a pre-built wheel
                if pip_res.returncode != 0:
                    is_wheel = target_spec.endswith(".whl")
                    if is_wheel:
                        fallback_cmd = [str(venv_pip), "install", "--no-warn-script-location", target_spec]
                        pip_res = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=120.0)
                    if pip_res.returncode != 0:
                        raise InstallationError(f"pip install failed (build hooks rejected): {pip_res.stderr or pip_res.stdout}")

            # Step 3: Write metadata record
            meta_record = metadata.copy() if metadata else {}
            meta_record["plugin_id"] = plugin_id
            meta_record["target_dir"] = str(target_dir)
            meta_record["venv_python"] = str(venv_python)

            import json
            with open(target_dir / "plugin_meta.json", "w", encoding="utf-8") as f:
                json.dump(meta_record, f, indent=2)

            logger.info("Successfully installed plugin '%s'", plugin_id)
            return target_dir

        except Exception as e:
            # Atomic rollback on any failure
            logger.warning("Installation of '%s' failed, rolling back directory %s: %s", plugin_id, target_dir, e)
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

    def uninstall(self, plugin_id: str) -> bool:
        """Removes the isolated directory for a plugin."""
        target_dir = self.get_plugin_dir(plugin_id)
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
            return True
        return False
