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
import re
from pathlib import Path

logger = logging.getLogger("optimus.security.credentials")

SERVICE_NAME = "OPTIMUS_AI"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"

# Standard provider key mappings to environment variables
ENV_FALLBACKS: dict[str, list[str]] = {
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "nvidia": ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"],
    "nvidia_nim": ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"],
    "custom": ["CUSTOM_API_KEY", "OPENAI_API_KEY"],
    "google_client_id": ["GOOGLE_CLIENT_ID", "GMAIL_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID"],
    "google_client_secret": ["GOOGLE_CLIENT_SECRET", "GMAIL_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_SECRET"],
}

# Regex patterns for redacting sensitive API keys from logs and diagnostics
_SECRET_PATTERNS = [
    re.compile(r"nvapi-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z-_]{35}"),
]


def redact_secret(text: str) -> str:
    """Scrub known API key patterns from an error message, URL, or diagnostic string."""
    if not text:
        return ""
    result = str(text)
    for pat in _SECRET_PATTERNS:
        result = pat.sub("[REDACTED_API_KEY]", result)
    return result


class SecretRedactionFilter(logging.Filter):
    """Logging filter ensuring secrets are never persisted to log files or terminal output."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secret(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: (redact_secret(v) if isinstance(v, str) else v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(redact_secret(v) if isinstance(v, str) else v for v in record.args)
        return True


# Install redaction filter on security logger and root optimus logger
_redaction_filter = SecretRedactionFilter()
logger.addFilter(_redaction_filter)
logging.getLogger("optimus").addFilter(_redaction_filter)


class CredentialVault:
    """Fallback encrypted file-based vault when OS keyring backend is unavailable."""

    def __init__(self, vault_dir: Path | None = None) -> None:
        self.vault_dir = vault_dir or _DEFAULT_CONFIG_DIR
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
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to decrypt fallback credential vault: {e}")
            return {}

    def write_all(self, data: dict[str, str]) -> bool:
        try:
            f = self._get_fernet()
            encrypted = f.encrypt(json.dumps(data).encode("utf-8"))
            self.vault_file.write_bytes(encrypted)
            return True
        except Exception as e:  # noqa: BLE001
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


def _canonical_aliases(provider: str) -> list[str]:
    """Return ordered lookup names for a given provider, handling synonyms and catalog IDs."""
    p = provider.strip().lower()
    if p in ("nvidia", "nvidia_nim"):
        return ["nvidia", "nvidia_nim"]
    return [p]


def store_credential(provider: str, secret: str) -> bool:
    """Store an API credential securely in OS Keyring with encrypted vault fallback."""
    aliases = _canonical_aliases(provider)
    primary = aliases[0]
    secret_clean = secret.strip() if secret else ""
    if not secret_clean:
        return delete_credential(provider)

    # 1. Try OS Keyring for all aliases
    success = False
    try:
        import keyring
        for name in aliases:
            keyring.set_password(SERVICE_NAME, name, secret_clean)
        logger.info(f"Credential for '{primary}' securely saved in OS keyring.")
        success = True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"OS keyring unavailable ({e}), falling back to machine-encrypted vault.")

    # 2. Encrypted Vault Fallback (sync all aliases)
    for name in aliases:
        _fallback_vault.set(name, secret_clean)

    return True


def get_credential(provider: str) -> str | None:
    """Retrieve credential from OS Keyring, encrypted vault, or environment variables."""
    aliases = _canonical_aliases(provider)

    # 1. Try OS Keyring
    for name in aliases:
        try:
            import keyring
            val = keyring.get_password(SERVICE_NAME, name)
            if val and val.strip():
                return val.strip()
        except Exception:  # noqa: BLE001, S110
            pass

    # 2. Try Encrypted Vault
    for name in aliases:
        vault_val = _fallback_vault.get(name)
        if vault_val and vault_val.strip():
            return vault_val.strip()

    # 3. Try Environment Variables
    for name in aliases:
        env_keys = ENV_FALLBACKS.get(name, [f"{name.upper()}_API_KEY"])
        for env_k in env_keys:
            val = os.environ.get(env_k)
            if val and val.strip():
                return val.strip()

    # 4. Legacy config/api_keys.json fallback for backward compatibility
    try:
        cfg_path = _DEFAULT_CONFIG_DIR / "api_keys.json"
        if not cfg_path.exists():
            cfg_path = Path.cwd() / "config" / "api_keys.json"
        if cfg_path.exists():
            cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
            for name in aliases:
                if name == "gemini" and cfg_data.get("gemini_api_key"):
                    return str(cfg_data["gemini_api_key"]).strip()
                if cfg_data.get(name):
                    return str(cfg_data[name]).strip()
                if cfg_data.get(f"{name}_api_key"):
                    return str(cfg_data[f"{name}_api_key"]).strip()
    except Exception:  # noqa: BLE001, S110
        pass

    return None


def delete_credential(provider: str) -> bool:
    """Remove a credential from both OS Keyring and fallback vault."""
    aliases = _canonical_aliases(provider)
    for name in aliases:
        try:
            import keyring
            keyring.delete_password(SERVICE_NAME, name)
        except Exception:  # noqa: BLE001, S110
            pass
        _fallback_vault.delete(name)
    return True


def mask_credential(secret: str | None) -> str:
    """Mask a secret string for safe UI presentation (e.g., 'sk-...4a1b' or 'nvapi-...4a1b')."""
    if not secret:
        return "Not Set"
    s = str(secret).strip()
    if len(s) <= 8:
        return "••••••••"
    return f"{s[:6]}...{s[-4:]}"


def list_configured_providers() -> list[str]:
    """Check which providers currently have stored or environment credentials."""
    providers = ["gemini", "openai", "anthropic", "groq", "openrouter", "nvidia", "custom"]
    configured = []
    for p in providers:
        if get_credential(p):
            configured.append(p)
    return configured
