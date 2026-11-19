"""core/security/credentials.py — Secure OS Credential Store & Key Management for OPTIMUS.

Implements:
- OS-level secure credential storage via Keyring (Windows Credential Manager, macOS Keychain, Linux SecretService).
- Fallback machine-bound encrypted vault (Fernet AES-128-CBC) for headless/restricted environments.
- Fallback resolution from environment variables (GEMINI_API_KEY, OPENAI_API_KEY, etc.).
- Strict masking and zero-plaintext leakage in logs, UI, or Git-tracked files.
"""

from __future__ import annotations

import base64
import getpass
import json
import logging
import os
import platform
from pathlib import Path

logger = logging.getLogger("optimus.security.credentials")

SERVICE_NAME = "OPTIMUS_AI"

# Standard provider key mappings to environment variables
ENV_FALLBACKS: dict[str, list[str]] = {
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "custom": ["CUSTOM_API_KEY", "OPENAI_API_KEY"],
}


class CredentialVault:
    """Fallback encrypted file-based vault when OS keyring backend is unavailable."""

    def __init__(self, vault_dir: Path | None = None) -> None:
        self.vault_dir = vault_dir or (Path.cwd() / "config")
        self.vault_file = self.vault_dir / ".vault.enc"
        self.salt_file = self.vault_dir / ".vault.salt"
        self._fernet = None

    def _get_fernet(self):
        if self._fernet is not None:
            return self._fernet

        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        self.vault_dir.mkdir(parents=True, exist_ok=True)
        if self.salt_file.exists():
            salt = self.salt_file.read_bytes()
        else:
            salt = os.urandom(16)
            self.salt_file.write_bytes(salt)

        machine_seed = f"{getpass.getuser()}-{platform.node()}-{platform.machine()}".encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(machine_seed))
        self._fernet = Fernet(key)
        return self._fernet

    def read_all(self) -> dict[str, str]:
        if not self.vault_file.exists():
            return {}
        try:
            f = self._get_fernet()
            data = f.decrypt(self.vault_file.read_bytes())
            return json.loads(data.decode("utf-8"))
        except Exception as e:
            logger.warning(f"Failed to decrypt fallback credential vault: {e}")
            return {}

    def write_all(self, data: dict[str, str]) -> bool:
        try:
            f = self._get_fernet()
            encrypted = f.encrypt(json.dumps(data).encode("utf-8"))
            self.vault_file.write_bytes(encrypted)
            return True
        except Exception as e:
            logger.error(f"Failed to write fallback credential vault: {e}")
            return False

    def get(self, key: str) -> str | None:
        return self.read_all().get(key)

    def set(self, key: str, value: str) -> bool:
        data = self.read_all()
        data[key] = value
        return self.write_all(data)

    def delete(self, key: str) -> bool:
        data = self.read_all()
        if key in data:
            del data[key]
            return self.write_all(data)
        return True


_fallback_vault = CredentialVault()


def store_credential(provider: str, secret: str) -> bool:
    """Store an API credential securely in OS Keyring with encrypted vault fallback."""
    provider_clean = provider.strip().lower()
    if not secret:
        return delete_credential(provider_clean)

    # 1. Try OS Keyring
    try:
        import keyring
        keyring.set_password(SERVICE_NAME, provider_clean, secret)
        logger.info(f"Credential for '{provider_clean}' securely saved in OS keyring.")
        return True
    except Exception as e:
        logger.warning(f"OS keyring unavailable ({e}), falling back to machine-encrypted vault.")

    # 2. Encrypted Vault Fallback
    return _fallback_vault.set(provider_clean, secret)


def get_credential(provider: str) -> str | None:
    """Retrieve credential from OS Keyring, encrypted vault, or environment variables."""
    provider_clean = provider.strip().lower()

    # 1. Try OS Keyring
    try:
        import keyring
        val = keyring.get_password(SERVICE_NAME, provider_clean)
        if val:
            return val
    except Exception:
        pass

    # 2. Try Encrypted Vault
    vault_val = _fallback_vault.get(provider_clean)
    if vault_val:
        return vault_val

    # 3. Try Environment Variables
    env_keys = ENV_FALLBACKS.get(provider_clean, [f"{provider_clean.upper()}_API_KEY"])
    for env_k in env_keys:
        val = os.environ.get(env_k)
        if val:
            return val

    # 4. Legacy config/api_keys.json fallback for backward compatibility
    try:
        cfg_path = Path.cwd() / "config" / "api_keys.json"
        if cfg_path.exists():
            cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if provider_clean == "gemini" and cfg_data.get("gemini_api_key"):
                return cfg_data["gemini_api_key"]
            if cfg_data.get(f"{provider_clean}_api_key"):
                return cfg_data[f"{provider_clean}_api_key"]
    except Exception:
        pass

    return None


def delete_credential(provider: str) -> bool:
    """Remove a credential from both OS Keyring and fallback vault."""
    provider_clean = provider.strip().lower()
    success = True

    try:
        import keyring
        keyring.delete_password(SERVICE_NAME, provider_clean)
    except Exception:
        pass

    _fallback_vault.delete(provider_clean)
    return success


def mask_credential(secret: str | None) -> str:
    """Mask a secret string for safe UI presentation (e.g., 'sk-...4a1b')."""
    if not secret:
        return "Not Set"
    s = str(secret).strip()
    if len(s) <= 8:
        return "••••••••"
    return f"{s[:4]}...{s[-4:]}"


def list_configured_providers() -> list[str]:
    """Check which providers currently have stored or environment credentials."""
    providers = ["gemini", "openai", "anthropic", "groq", "openrouter", "custom"]
    configured = []
    for p in providers:
        if get_credential(p):
            configured.append(p)
    return configured
