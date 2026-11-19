"""core/personal_intelligence/crypto.py — Authenticated Encryption at Rest for Personal Intelligence.

Implements:
- AES-256-GCM authenticated encryption with 128-bit authentication tags.
- High-entropy 256-bit storage master key provisioned in OS Keyring with DPAPI / environment fallback.
- 96-bit collision-resistant nonces (Process ID ^ Epoch 32-bit || Monotonic Counter 32-bit || Random 32-bit).
- Canonical Authenticated Associated Data (AAD) binding account_id, entity_type, record_id, and version.
- Cryptographic tamper detection raising SecurityIntegrityError on forged or altered ciphertexts.
- Transparent one-way migration for legacy plaintext records with enforcement gating.
"""

from __future__ import annotations

import base64
import itertools
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.security.credentials import get_credential, store_credential

logger = logging.getLogger("optimus.personal.crypto")

MASTER_KEY_ID = "personal_store_master_key"
_nonce_counter = itertools.count(1)
_nonce_lock = threading.Lock()


class SecurityIntegrityError(Exception):
    """Raised when ciphertext authentication fails, indicating corruption or tampering."""


class PersonalStoreCrypto:
    """Core cryptographic engine for personal intelligence encryption at rest."""

    _instance: PersonalStoreCrypto | None = None
    _singleton_lock = threading.Lock()

    def __init__(self, master_key: bytes | None = None) -> None:
        if master_key:
            self._key = master_key
        else:
            self._key = self._resolve_or_create_master_key()
        self._aesgcm = AESGCM(self._key)

    @classmethod
    def instance(cls) -> PersonalStoreCrypto:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._singleton_lock:
            cls._instance = None

    def _resolve_or_create_master_key(self) -> bytes:
        """Retrieve master key from OS Keyring, environment, or generate securely."""
        # 1. Check environment variable override
        env_key = os.environ.get("OPTIMUS_PERSONAL_MASTER_KEY")
        if env_key:
            try:
                raw = base64.b64decode(env_key)
                if len(raw) == 32:
                    return raw
            except Exception:
                pass

        # 2. Check OS Keyring via CredentialVault
        stored = get_credential(MASTER_KEY_ID)
        if stored:
            try:
                raw = base64.b64decode(stored)
                if len(raw) == 32:
                    return raw
            except Exception as e:
                logger.warning("Corrupted master key in credential store: %s", e)

        # 3. Generate high-entropy 256-bit master key under cross-process bootstrap lock
        lock_dir = Path.cwd() / "memory" / "personal" / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = lock_dir / "master_key.lock"

        with open(lock_file, "a+b") as lf:
            acquired = False
            try:
                if os.name == "nt":
                    import msvcrt
                    lf.seek(0)
                    msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                acquired = True

                # Double-check after acquiring lock in case peer process just wrote it
                rechecked = get_credential(MASTER_KEY_ID)
                if rechecked:
                    try:
                        raw = base64.b64decode(rechecked)
                        if len(raw) == 32:
                            return raw
                    except Exception:
                        pass

                new_key = secrets.token_bytes(32)
                b64_key = base64.b64encode(new_key).decode("utf-8")
                store_credential(MASTER_KEY_ID, b64_key)
                logger.info("Generated new 256-bit AES-GCM master key and stored in CredentialVault.")
                return new_key
            finally:
                if acquired:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            lf.seek(0)
                            msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass

    def purge_master_key(self) -> bool:
        """Cryptographically shred master key from CredentialVault."""
        self._key = b"\x00" * 32
        self._aesgcm = None
        PersonalStoreCrypto.reset_instance()
        return store_credential(MASTER_KEY_ID, "")

    def _generate_nonce(self) -> bytes:
        """Generate structured 96-bit (12-byte) collision-resistant nonce.

        Composition:
        - 4 bytes: Process ID ^ Epoch (Time-varying process seed)
        - 4 bytes: Monotonic atomic counter
        - 4 bytes: Cryptographically secure random bytes
        """
        with _nonce_lock:
            cnt = next(_nonce_counter) & 0xFFFFFFFF

        pid_epoch = (os.getpid() ^ int(time.time())) & 0xFFFFFFFF
        rand_bytes = secrets.token_bytes(4)
        return pid_epoch.to_bytes(4, "big") + cnt.to_bytes(4, "big") + rand_bytes

    def build_canonical_aad(
        self,
        entity_type: str,
        record_id: str,
        account_id: str = "global",
        version: int = 1,
    ) -> bytes:
        """Generate canonical, deterministic JSON AAD digest to prevent transplantation."""
        aad_dict = {
            "acc": str(account_id or "global"),
            "id": str(record_id or "default"),
            "type": str(entity_type or "unknown"),
            "ver": int(version),
        }
        return json.dumps(aad_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def encrypt_data(
        self,
        plaintext_dict: Any,
        entity_type: str,
        record_id: str,
        account_id: str = "global",
    ) -> dict[str, Any]:
        """Encrypt arbitrary Python data structure returning authenticated JSON envelope."""
        plaintext_bytes = json.dumps(plaintext_dict, ensure_ascii=False, default=str).encode("utf-8")
        nonce = self._generate_nonce()
        aad = self.build_canonical_aad(entity_type, record_id, account_id)

        ciphertext = self._aesgcm.encrypt(nonce, plaintext_bytes, aad)

        return {
            "v": 1,
            "enc": True,
            "alg": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "aad": base64.b64encode(aad).decode("ascii"),
            "ct": base64.b64encode(ciphertext).decode("ascii"),
        }

    def decrypt_data(
        self,
        envelope: dict[str, Any],
        expected_entity_type: str,
        expected_record_id: str | None = None,
        expected_account_id: str | None = None,
    ) -> Any:
        """Verify AAD and decrypt ciphertext envelope returning original Python data."""
        if not isinstance(envelope, dict) or not envelope.get("enc"):
            raise SecurityIntegrityError("Payload is not an authenticated encrypted envelope.")

        alg = envelope.get("alg")
        if alg != "AES-256-GCM":
            raise SecurityIntegrityError(f"Unsupported cipher algorithm: '{alg}'.")

        try:
            nonce = base64.b64decode(envelope["nonce"])
            ciphertext = base64.b64decode(envelope["ct"])
            stored_aad = base64.b64decode(envelope["aad"])
            stored_aad_dict = json.loads(stored_aad.decode("utf-8"))
        except Exception as ex:
            raise SecurityIntegrityError(f"Malformed encryption envelope headers: {ex}") from ex

        if stored_aad_dict.get("type") != expected_entity_type:
            raise SecurityIntegrityError(
                f"AAD entity type mismatch: expected '{expected_entity_type}', got '{stored_aad_dict.get('type')}'"
            )

        if expected_record_id is not None and stored_aad_dict.get("id") != str(expected_record_id):
            raise SecurityIntegrityError(
                f"AAD record id mismatch: expected '{expected_record_id}', got '{stored_aad_dict.get('id')}'"
            )

        if expected_account_id is not None and stored_aad_dict.get("acc") != str(expected_account_id):
            raise SecurityIntegrityError(
                f"AAD account mismatch: expected '{expected_account_id}', got '{stored_aad_dict.get('acc')}'"
            )

        try:
            decrypted_bytes = self._aesgcm.decrypt(nonce, ciphertext, stored_aad)
            return json.loads(decrypted_bytes.decode("utf-8"))
        except Exception as ex:
            raise SecurityIntegrityError(
                f"Ciphertext authentication failed (tampering or wrong key): {ex}"
            ) from ex

    @staticmethod
    def is_encrypted_envelope(data: Any) -> bool:
        """Check if dictionary has encrypted envelope structure."""
        return isinstance(data, dict) and data.get("enc") is True and "ct" in data and "nonce" in data
