"""core/personal_intelligence/storage.py — Multiprocess-Safe Encrypted Persistent Store.

Implements:
- Thread-safe and multiprocess-safe multi-category storage under `memory/personal/`.
- AES-256-GCM authenticated encryption at rest via PersonalStoreCrypto with AAD.
- OS-level category file locking (.locks/<category>.lock) preventing lost updates.
- Read-modify-write `mutate_*` patterns ensuring cross-process data integrity.
- Multi-tenant stream cursor management in `sync_state.json`.
- Idempotent deduplication for tasks (`find_duplicate_task`) and commitments (`find_duplicate_commitment`).
- Non-destructive plaintext-to-ciphertext migration with strict downgrade enforcement.
- Safe tempfile atomic replacements and orphaned tempfile sweeps.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.personal_intelligence.crypto import (
    PersonalStoreCrypto,
    SecurityIntegrityError,
)
from core.personal_intelligence.types import (
    Account,
    Commitment,
    Conversation,
    Message,
    PersonalEvent,
    PersonalTask,
    PersonContact,
    ProviderType,
    TaskStatus,
)

logger = logging.getLogger("optimus.personal.storage")

_thread_local = threading.local()


def _get_held_locks() -> set[str]:
    """Retrieve the set of category lock keys currently held by the active thread."""
    if not hasattr(_thread_local, "held_locks"):
        _thread_local.held_locks = set()
    return _thread_local.held_locks


def mask_pii_string(text: str) -> str:
    """Mask email addresses and sensitive identifiers in log outputs."""
    if not text:
        return ""
    return re.sub(
        r"([a-zA-Z0-9_.+-])[a-zA-Z0-9_.+-]*@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
        r"\1***@\2",
        text,
    )


def _atomic_write_json(target_path: Path, data: Any, max_retries: int = 6) -> None:
    """Write data to target_path atomically using a per-write temp file with Windows backoff."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f".{target_path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    temp_path.write_text(text, encoding="utf-8")

    base_delay = 0.02
    for attempt in range(max_retries):
        try:
            os.replace(temp_path, target_path)
            return
        except (PermissionError, FileExistsError, OSError) as err:
            if attempt == max_retries - 1:
                logger.error("Failed atomic write to %s after %d retries: %s", target_path, max_retries, err)
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            delay = base_delay * (2 ** attempt) + (secrets.randbelow(15) / 1000.0)
            time.sleep(delay)


class PersonalStore:
    """Multiprocess-safe, local-first encrypted persistent repository for personal intelligence."""

    _instance: PersonalStore | None = None
    _singleton_lock = threading.Lock()

    def __init__(
        self,
        root_dir: Path | str | None = None,
        crypto: PersonalStoreCrypto | None = None,
    ) -> None:
        if root_dir:
            self.root = Path(root_dir)
        else:
            self.root = Path.cwd() / "memory" / "personal"

        self.root.mkdir(parents=True, exist_ok=True)
        self.messages_dir = self.root / "messages"
        self.messages_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir = self.root / ".locks"
        self.locks_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._crypto = crypto or PersonalStoreCrypto.instance()
        self._enforce_sentinel = self.root / ".encryption_enforced"

        # In-memory entity registries
        self._accounts: dict[str, Account] = {}
        self._tasks: dict[str, PersonalTask] = {}
        self._events: dict[str, PersonalEvent] = {}
        self._commitments: dict[str, Commitment] = {}
        self._contacts: dict[str, PersonContact] = {}
        self._messages: dict[str, Message] = {}
        self._conversations: dict[str, Conversation] = {}
        self._sync_state: dict[str, Any] = {"accounts": {}}

        self._cleanup_orphaned_tmp_files()
        self._load_all()

    @classmethod
    def instance(cls, root_dir: Path | str | None = None) -> PersonalStore:
        """Singleton accessor."""
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(root_dir)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (useful in test harnesses)."""
        with cls._singleton_lock:
            cls._instance = None

    # ── Inter-Process Category File Locking ───────────────────────────────────

    @contextmanager
    def lock_category(self, category: str, timeout: float = 10.0) -> Generator[None, None, None]:
        """Acquire an exclusive OS-level inter-process lock on an entity category."""
        held = _get_held_locks()
        if category in held:
            yield
            return

        lock_path = self.locks_dir / f"{category}.lock"
        with open(lock_path, "a+b") as f:
            start = time.perf_counter()
            acquired = False
            try:
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            f.seek(0)
                            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        held.add(category)
                        break
                    except (OSError, PermissionError):
                        if (time.perf_counter() - start) >= timeout:
                            raise TimeoutError(
                                f"Timed out waiting for file lock on category '{category}' after {timeout:.1f}s"
                            ) from None
                        time.sleep(0.01 + (secrets.randbelow(15) / 1000.0))
                yield
            finally:
                if acquired:
                    held.discard(category)
                    try:
                        if os.name == "nt":
                            import msvcrt
                            f.seek(0)
                            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass

    # ── Encryption & Persistence Helpers ──────────────────────────────────────

    def is_encryption_enforced(self) -> bool:
        """Check if unencrypted legacy data is strictly rejected."""
        return self._enforce_sentinel.exists()

    def enforce_encryption(self) -> None:
        """Mark store as encryption-enforced, rejecting unauthenticated plaintext reads."""
        self._enforce_sentinel.write_text("enforced=true\n", encoding="utf-8")

    def _read_encrypted_or_plaintext(
        self,
        path: Path,
        expected_entity_type: str,
        expected_record_id: str | None = None,
        expected_account_id: str | None = None,
    ) -> Any:
        """Read file, authenticating and decrypting if encrypted, or parsing plaintext if allowed."""
        if not path.is_file():
            return None

        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)

        if PersonalStoreCrypto.is_encrypted_envelope(data):
            return self._crypto.decrypt_data(
                envelope=data,
                expected_entity_type=expected_entity_type,
                expected_record_id=expected_record_id,
                expected_account_id=expected_account_id,
            )

        if self.is_encryption_enforced():
            raise SecurityIntegrityError(
                f"Plaintext data found for {path.name} while encryption is strictly enforced."
            )

        return data

    def _atomic_write_encrypted(
        self,
        target_path: Path,
        data: Any,
        entity_type: str,
        record_id: str = "default",
        account_id: str = "global",
    ) -> None:
        """Encrypt and atomically write data to target path with AAD."""
        envelope = self._crypto.encrypt_data(
            plaintext_dict=data,
            entity_type=entity_type,
            record_id=record_id,
            account_id=account_id,
        )
        _atomic_write_json(target_path, envelope)

    def _cleanup_orphaned_tmp_files(self, max_age_seconds: float = 300.0) -> int:
        """Sweep and delete abandoned temporary files from crashes or interrupted writes."""
        now = time.time()
        cleaned = 0
        search_dirs = [self.root, self.messages_dir, self.locks_dir]

        for s_dir in search_dirs:
            if not s_dir.exists():
                continue
            for item in s_dir.glob("*.tmp"):
                try:
                    mtime = item.stat().st_mtime
                    if (now - mtime) > max_age_seconds:
                        item.unlink(missing_ok=True)
                        cleaned += 1
                except OSError:
                    pass
        return cleaned

    # ── Hydration from Disk ───────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Hydrate in-memory registry from disk upon initialization."""
        with self._lock:
            # 1. Accounts
            acc_file = self.root / "accounts.json"
            if acc_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        acc_file, expected_entity_type="accounts", expected_record_id="accounts"
                    )
                    if isinstance(raw, list):
                        self._accounts = {
                            acc.account_id: acc
                            for item in raw
                            if (acc := Account.from_dict(item))
                        }
                except SecurityIntegrityError:
                    raise
                except Exception as ex:
                    logger.warning("Failed loading accounts: %s", ex)

            # 2. Tasks
            task_file = self.root / "tasks.json"
            if task_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        task_file, expected_entity_type="tasks", expected_record_id="tasks"
                    )
                    if isinstance(raw, list):
                        self._tasks = {
                            t.task_id: t
                            for item in raw
                            if (t := PersonalTask.from_dict(item))
                        }
                except SecurityIntegrityError:
                    raise
                except Exception as ex:
                    logger.warning("Failed loading tasks: %s", ex)

            # 3. Events
            evt_file = self.root / "events.json"
            if evt_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        evt_file, expected_entity_type="events", expected_record_id="events"
                    )
                    if isinstance(raw, list):
                        self._events = {
                            e.event_id: e
                            for item in raw
                            if (e := PersonalEvent.from_dict(item))
                        }
                except SecurityIntegrityError:
                    raise
                except Exception as ex:
                    logger.warning("Failed loading events: %s", ex)

            # 4. Commitments
            com_file = self.root / "commitments.json"
            if com_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        com_file, expected_entity_type="commitments", expected_record_id="commitments"
                    )
                    if isinstance(raw, list):
                        self._commitments = {
                            c.commitment_id: c
                            for item in raw
                            if (c := Commitment.from_dict(item))
                        }
                except SecurityIntegrityError:
                    raise
                except Exception as ex:
                    logger.warning("Failed loading commitments: %s", ex)

            # 5. Contacts
            con_file = self.root / "contacts.json"
            if con_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        con_file, expected_entity_type="contacts", expected_record_id="contacts"
                    )
                    if isinstance(raw, list):
                        self._contacts = {
                            con.contact_id: con
                            for item in raw
                            if (con := PersonContact.from_dict(item))
                        }
                except SecurityIntegrityError:
                    raise
                except Exception as ex:
                    logger.warning("Failed loading contacts: %s", ex)

            # 6. Sync State
            sync_file = self.root / "sync_state.json"
            if sync_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        sync_file, expected_entity_type="sync_state", expected_record_id="sync_state"
                    )
                    if isinstance(raw, dict):
                        self._sync_state = raw
                except SecurityIntegrityError:
                    raise
                except Exception as ex:
                    logger.warning("Failed loading sync_state: %s", ex)
                    self._sync_state = {"accounts": {}}

            # 7. Messages
            for m_path in self.messages_dir.glob("*.json"):
                try:
                    raw = self._read_encrypted_or_plaintext(
                        m_path,
                        expected_entity_type="message",
                        expected_record_id=m_path.stem,
                    )
                    if isinstance(raw, dict):
                        msg = Message.from_dict(raw)
                        self._messages[msg.message_id] = msg
                except SecurityIntegrityError:
                    raise
                except Exception as ex:
                    logger.debug("Failed loading message %s: %s", m_path.name, ex)

    # ── Multiprocess Mutate Helpers ──────────────────────────────────────────

    def mutate_accounts(self, updater: Callable[[dict[str, Account]], None]) -> dict[str, Account]:
        """Atomically reload accounts from disk under category lock, apply updater, and save."""
        with self.lock_category("accounts"), self._lock:
            acc_file = self.root / "accounts.json"
            if acc_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        acc_file, expected_entity_type="accounts", expected_record_id="accounts"
                    )
                    if isinstance(raw, list):
                        self._accounts = {
                            acc.account_id: acc
                            for item in raw
                            if (acc := Account.from_dict(item))
                        }
                except Exception as ex:
                    logger.warning("Failed reading accounts during mutate: %s", ex)

            updater(self._accounts)
            data = [acc.to_dict() for acc in self._accounts.values()]
            self._atomic_write_encrypted(
                acc_file, data, entity_type="accounts", record_id="accounts", account_id="global"
            )
            return dict(self._accounts)

    def mutate_tasks(self, updater: Callable[[dict[str, PersonalTask]], None]) -> dict[str, PersonalTask]:
        """Atomically reload tasks from disk under category lock, apply updater, and save."""
        with self.lock_category("tasks"), self._lock:
            task_file = self.root / "tasks.json"
            if task_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        task_file, expected_entity_type="tasks", expected_record_id="tasks"
                    )
                    if isinstance(raw, list):
                        self._tasks = {
                            t.task_id: t
                            for item in raw
                            if (t := PersonalTask.from_dict(item))
                        }
                except Exception as ex:
                    logger.warning("Failed reading tasks during mutate: %s", ex)

            updater(self._tasks)
            data = [t.to_dict() for t in self._tasks.values()]
            self._atomic_write_encrypted(
                task_file, data, entity_type="tasks", record_id="tasks", account_id="global"
            )
            return dict(self._tasks)

    def mutate_events(self, updater: Callable[[dict[str, PersonalEvent]], None]) -> dict[str, PersonalEvent]:
        """Atomically reload events from disk under category lock, apply updater, and save."""
        with self.lock_category("events"), self._lock:
            evt_file = self.root / "events.json"
            if evt_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        evt_file, expected_entity_type="events", expected_record_id="events"
                    )
                    if isinstance(raw, list):
                        self._events = {
                            e.event_id: e
                            for item in raw
                            if (e := PersonalEvent.from_dict(item))
                        }
                except Exception as ex:
                    logger.warning("Failed reading events during mutate: %s", ex)

            updater(self._events)
            data = [e.to_dict() for e in self._events.values()]
            self._atomic_write_encrypted(
                evt_file, data, entity_type="events", record_id="events", account_id="global"
            )
            return dict(self._events)

    def mutate_commitments(self, updater: Callable[[dict[str, Commitment]], None]) -> dict[str, Commitment]:
        """Atomically reload commitments from disk under category lock, apply updater, and save."""
        with self.lock_category("commitments"), self._lock:
            com_file = self.root / "commitments.json"
            if com_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        com_file, expected_entity_type="commitments", expected_record_id="commitments"
                    )
                    if isinstance(raw, list):
                        self._commitments = {
                            c.commitment_id: c
                            for item in raw
                            if (c := Commitment.from_dict(item))
                        }
                except Exception as ex:
                    logger.warning("Failed reading commitments during mutate: %s", ex)

            updater(self._commitments)
            data = [c.to_dict() for c in self._commitments.values()]
            self._atomic_write_encrypted(
                com_file, data, entity_type="commitments", record_id="commitments", account_id="global"
            )
            return dict(self._commitments)

    def mutate_contacts(self, updater: Callable[[dict[str, PersonContact]], None]) -> dict[str, PersonContact]:
        """Atomically reload contacts from disk under category lock, apply updater, and save."""
        with self.lock_category("contacts"), self._lock:
            con_file = self.root / "contacts.json"
            if con_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        con_file, expected_entity_type="contacts", expected_record_id="contacts"
                    )
                    if isinstance(raw, list):
                        self._contacts = {
                            con.contact_id: con
                            for item in raw
                            if (con := PersonContact.from_dict(item))
                        }
                except Exception as ex:
                    logger.warning("Failed reading contacts during mutate: %s", ex)

            updater(self._contacts)
            data = [c.to_dict() for c in self._contacts.values()]
            self._atomic_write_encrypted(
                con_file, data, entity_type="contacts", record_id="contacts", account_id="global"
            )
            return dict(self._contacts)

    def mutate_sync_state(self, updater: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """Atomically reload sync_state from disk under category lock, apply updater, and save."""
        with self.lock_category("sync_state"), self._lock:
            sync_file = self.root / "sync_state.json"
            if sync_file.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        sync_file, expected_entity_type="sync_state", expected_record_id="sync_state"
                    )
                    if isinstance(raw, dict):
                        self._sync_state = raw
                except Exception as ex:
                    logger.warning("Failed reading sync_state during mutate: %s", ex)

            updater(self._sync_state)
            self._atomic_write_encrypted(
                sync_file,
                self._sync_state,
                entity_type="sync_state",
                record_id="sync_state",
                account_id="global",
            )
            return dict(self._sync_state)

    # ── Accounts ─────────────────────────────────────────────────────────────

    def save_account(self, account: Account) -> None:
        """Persist or update an account record safely across processes."""
        def _updater(accs: dict[str, Account]) -> None:
            accs[account.account_id] = account

        self.mutate_accounts(_updater)

    def get_account(self, account_id: str) -> Account | None:
        with self._lock:
            return self._accounts.get(account_id)

    def list_accounts(self, provider: ProviderType | None = None) -> list[Account]:
        with self._lock:
            accs = list(self._accounts.values())
            if provider:
                return [a for a in accs if a.provider == provider]
            return accs

    def delete_account(self, account_id: str) -> bool:
        deleted = False

        def _updater_acc(accs: dict[str, Account]) -> None:
            nonlocal deleted
            if account_id in accs:
                del accs[account_id]
                deleted = True

        self.mutate_accounts(_updater_acc)

        if deleted:
            def _updater_sync(sync_st: dict[str, Any]) -> None:
                if "accounts" in sync_st and account_id in sync_st["accounts"]:
                    del sync_st["accounts"][account_id]

            self.mutate_sync_state(_updater_sync)

        return deleted

    # ── Sync Cursors (Multi-Tenant) ──────────────────────────────────────────

    def get_stream_cursor(self, account_id: str, stream_key: str) -> dict[str, Any] | None:
        """Retrieve multi-tenant sync cursor for a specific stream."""
        with self._lock:
            acc_data = self._sync_state.get("accounts", {}).get(account_id, {})
            return acc_data.get("streams", {}).get(stream_key)

    def update_stream_cursor(
        self,
        account_id: str,
        stream_key: str,
        cursor: str,
        cursor_type: str = "historyId",
        status: str = "SUCCESS",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Atomically update multi-tenant stream cursor under sync_state lock."""
        def _updater(sync_st: dict[str, Any]) -> None:
            if "accounts" not in sync_st:
                sync_st["accounts"] = {}
            if account_id not in sync_st["accounts"]:
                sync_st["accounts"][account_id] = {"streams": {}}
            if "streams" not in sync_st["accounts"][account_id]:
                sync_st["accounts"][account_id]["streams"] = {}

            sync_st["accounts"][account_id]["streams"][stream_key] = {
                "cursor": cursor,
                "cursor_type": cursor_type,
                "last_synced_at": time.time(),
                "status": status,
                "metadata": metadata or {},
            }

        self.mutate_sync_state(_updater)

    def get_sync_cursor(self, account_id: str, stream_key: str) -> str | None:
        """Convenience method returning raw string cursor."""
        info = self.get_stream_cursor(account_id, stream_key)
        if info and isinstance(info, dict):
            return info.get("cursor")
        return None

    def set_sync_cursor(self, account_id: str, stream_key: str, cursor: str, cursor_type: str = "historyId") -> None:
        """Convenience method setting raw string cursor."""
        self.update_stream_cursor(account_id, stream_key, cursor, cursor_type=cursor_type)

    def get_accounts(self, *args: Any, **kwargs: Any) -> list[Account]:
        return self.list_accounts(*args, **kwargs)

    def get_messages(self, *args: Any, **kwargs: Any) -> list[Message]:
        return self.list_messages(*args, **kwargs)

    def get_tasks(self, *args: Any, **kwargs: Any) -> list[PersonalTask]:
        return self.list_tasks(*args, **kwargs)

    def get_events(self, *args: Any, **kwargs: Any) -> list[PersonalEvent]:
        return self.list_events(*args, **kwargs)

    def save_messages(self, messages: list[Message]) -> None:
        for m in messages:
            self.save_message(m)

    def save_tasks(self, tasks: list[PersonalTask]) -> None:
        def _updater(task_dict: dict[str, PersonalTask]) -> None:
            for t in tasks:
                task_dict[t.task_id] = t

        self.mutate_tasks(_updater)

    def save_events(self, events: list[PersonalEvent]) -> None:
        def _updater(evt_dict: dict[str, PersonalEvent]) -> None:
            for e in events:
                evt_dict[e.event_id] = e

        self.mutate_events(_updater)

    # ── Messages ─────────────────────────────────────────────────────────────

    def save_message(self, message: Message | dict[str, Any]) -> None:
        """Persist normalized message record encrypted under message lock."""
        if isinstance(message, dict):
            from core.personal_intelligence.types import Message
            message = Message.from_dict(message)
        with self.lock_category("messages"), self._lock:
            self._messages[message.message_id] = message
            msg_path = self.messages_dir / f"{message.message_id}.json"
            self._atomic_write_encrypted(
                msg_path,
                message.to_dict(),
                entity_type="message",
                record_id=message.message_id,
                account_id=message.account_id or "global",
            )

    def has_message(self, message_id: str) -> bool:
        """Check if message exists in memory or on disk."""
        with self._lock:
            if message_id in self._messages:
                return True
            return (self.messages_dir / f"{message_id}.json").exists()

    def get_message(self, message_id: str) -> Message | None:
        with self._lock:
            if message_id in self._messages:
                return self._messages[message_id]

            msg_path = self.messages_dir / f"{message_id}.json"
            if msg_path.is_file():
                try:
                    raw = self._read_encrypted_or_plaintext(
                        msg_path,
                        expected_entity_type="message",
                        expected_record_id=message_id,
                    )
                    if isinstance(raw, dict):
                        msg = Message.from_dict(raw)
                        self._messages[msg.message_id] = msg
                        return msg
                except Exception as ex:
                    logger.warning("Failed reading message %s: %s", message_id, ex)
            return None

    def list_messages(
        self,
        provider: ProviderType | None = None,
        account_id: str | None = None,
        is_unread: bool | None = None,
        is_important: bool | None = None,
        search_query: str | None = None,
        category: str | None = None,
        unread_only: bool | None = None,
        limit: int = 100,
    ) -> list[Message]:
        if unread_only is not None:
            is_unread = unread_only
        with self._lock:
            msgs = list(self._messages.values())
            if provider:
                msgs = [m for m in msgs if m.provider == provider]
            if account_id:
                msgs = [m for m in msgs if m.account_id == account_id]
            if is_unread is not None:
                msgs = [m for m in msgs if (not m.is_read) == is_unread]
            if is_important is not None:
                msgs = [m for m in msgs if m.is_important == is_important]
            if category:
                cat_low = category.lower()
                msgs = [m for m in msgs if any(cat_low in l.lower() for l in m.labels) or cat_low in m.subject.lower()]
            if search_query:
                q = search_query.lower()
                msgs = [m for m in msgs if q in m.subject.lower() or q in m.sender.lower() or q in m.body_snippet.lower()]
            msgs.sort(key=lambda m: m.timestamp, reverse=True)
            return msgs[:limit]

    # ── Tasks ────────────────────────────────────────────────────────────────

    def save_task(self, task: PersonalTask) -> None:
        """Persist or update extracted task safely across processes."""
        def _updater(tasks: dict[str, PersonalTask]) -> None:
            tasks[task.task_id] = task

        self.mutate_tasks(_updater)

    def get_task(self, task_id: str) -> PersonalTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def find_duplicate_task(self, dedup_key: str) -> PersonalTask | None:
        """Find active task matching dedup_key; completed/cancelled tasks ignored."""
        if not dedup_key:
            return None
        with self._lock:
            for t in self._tasks.values():
                if t.dedup_key == dedup_key and t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.WAITING):
                    return t
            return None

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        provider: ProviderType | None = None,
        overdue_only: bool = False,
    ) -> list[PersonalTask]:
        with self._lock:
            now = time.time()
            tasks = list(self._tasks.values())
            if status:
                tasks = [t for t in tasks if t.status == status]
            if provider:
                tasks = [t for t in tasks if t.source_provider == provider]
            if overdue_only:
                tasks = [t for t in tasks if t.due_date and t.due_date < now and t.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)]
            tasks.sort(key=lambda t: (t.due_date or float("inf"), -t.confidence))
            return tasks

    def delete_task(self, task_id: str) -> bool:
        deleted = False

        def _updater(tasks: dict[str, PersonalTask]) -> None:
            nonlocal deleted
            if task_id in tasks:
                del tasks[task_id]
                deleted = True

        self.mutate_tasks(_updater)
        return deleted

    # ── Events ───────────────────────────────────────────────────────────────

    def save_event(self, event: PersonalEvent) -> None:
        """Persist or update calendar event safely across processes."""
        def _updater(evts: dict[str, PersonalEvent]) -> None:
            evts[event.event_id] = event

        self.mutate_events(_updater)

    def get_event(self, event_id: str) -> PersonalEvent | None:
        with self._lock:
            return self._events.get(event_id)

    def list_events(
        self,
        start_min: float | None = None,
        start_max: float | None = None,
        provider: ProviderType | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> list[PersonalEvent]:
        if start_min is None and start_time is not None:
            try:
                start_min = float(start_time)
            except Exception:
                pass
        if start_max is None and end_time is not None:
            try:
                start_max = float(end_time)
            except Exception:
                pass
        with self._lock:
            evts = list(self._events.values())
            if provider:
                evts = [e for e in evts if e.provider == provider]
            if start_min is not None:
                evts = [e for e in evts if e.end_time >= start_min]
            if start_max is not None:
                evts = [e for e in evts if e.start_time <= start_max]
            evts.sort(key=lambda e: e.start_time)
            return evts

    def delete_event(self, event_id: str) -> bool:
        deleted = False

        def _updater(evts: dict[str, PersonalEvent]) -> None:
            nonlocal deleted
            if event_id in evts:
                del evts[event_id]
                deleted = True

        self.mutate_events(_updater)
        return deleted

    # ── Commitments ──────────────────────────────────────────────────────────

    def save_commitment(self, commitment: Commitment) -> None:
        """Persist or update commitment record safely across processes."""
        def _updater(comms: dict[str, Commitment]) -> None:
            comms[commitment.commitment_id] = commitment

        self.mutate_commitments(_updater)

    def find_duplicate_commitment(self, dedup_key: str) -> Commitment | None:
        """Find active commitment matching dedup_key."""
        if not dedup_key:
            return None
        with self._lock:
            for c in self._commitments.values():
                if getattr(c, "dedup_key", "") == dedup_key:
                    return c
            return None

    def list_commitments(self, counterpart: str | None = None) -> list[Commitment]:
        with self._lock:
            comms = list(self._commitments.values())
            if counterpart:
                c_low = counterpart.lower()
                comms = [c for c in comms if c_low in c.counterpart.lower() or c_low in c.counterpart_email.lower()]
            comms.sort(key=lambda c: (c.deadline or float("inf"), c.created_at))
            return comms

    # ── Contacts ─────────────────────────────────────────────────────────────

    def save_contact(self, contact: PersonContact) -> None:
        """Persist or update contact record safely across processes."""
        def _updater(contacts: dict[str, PersonContact]) -> None:
            contacts[contact.contact_id] = contact

        self.mutate_contacts(_updater)

    def get_contact_by_email(self, email: str) -> PersonContact | None:
        if not email:
            return None
        with self._lock:
            e_low = email.strip().lower()
            for c in self._contacts.values():
                if c.primary_email.lower() == e_low or any(em.lower() == e_low for em in c.all_emails):
                    return c
            return None

    def list_contacts(self) -> list[PersonContact]:
        with self._lock:
            return list(self._contacts.values())

    # ── Migration to Encrypted Format ─────────────────────────────────────────

    def migrate_plaintext_to_encrypted(self) -> dict[str, int]:
        """Inspect all storage files; convert any plaintext JSON to authenticated encrypted envelopes."""
        counts = {"accounts": 0, "tasks": 0, "events": 0, "commitments": 0, "contacts": 0, "sync_state": 0, "messages": 0}

        # 1. Accounts
        with self.lock_category("accounts"):
            acc_file = self.root / "accounts.json"
            if acc_file.is_file():
                raw = json.loads(acc_file.read_text(encoding="utf-8"))
                if not PersonalStoreCrypto.is_encrypted_envelope(raw):
                    self._atomic_write_encrypted(acc_file, raw, "accounts", "accounts")
                    counts["accounts"] += len(raw) if isinstance(raw, list) else 1

        # 2. Tasks
        with self.lock_category("tasks"):
            task_file = self.root / "tasks.json"
            if task_file.is_file():
                raw = json.loads(task_file.read_text(encoding="utf-8"))
                if not PersonalStoreCrypto.is_encrypted_envelope(raw):
                    self._atomic_write_encrypted(task_file, raw, "tasks", "tasks")
                    counts["tasks"] += len(raw) if isinstance(raw, list) else 1

        # 3. Events
        with self.lock_category("events"):
            evt_file = self.root / "events.json"
            if evt_file.is_file():
                raw = json.loads(evt_file.read_text(encoding="utf-8"))
                if not PersonalStoreCrypto.is_encrypted_envelope(raw):
                    self._atomic_write_encrypted(evt_file, raw, "events", "events")
                    counts["events"] += len(raw) if isinstance(raw, list) else 1

        # 4. Commitments
        with self.lock_category("commitments"):
            com_file = self.root / "commitments.json"
            if com_file.is_file():
                raw = json.loads(com_file.read_text(encoding="utf-8"))
                if not PersonalStoreCrypto.is_encrypted_envelope(raw):
                    self._atomic_write_encrypted(com_file, raw, "commitments", "commitments")
                    counts["commitments"] += len(raw) if isinstance(raw, list) else 1

        # 5. Contacts
        with self.lock_category("contacts"):
            con_file = self.root / "contacts.json"
            if con_file.is_file():
                raw = json.loads(con_file.read_text(encoding="utf-8"))
                if not PersonalStoreCrypto.is_encrypted_envelope(raw):
                    self._atomic_write_encrypted(con_file, raw, "contacts", "contacts")
                    counts["contacts"] += len(raw) if isinstance(raw, list) else 1

        # 6. Sync State
        with self.lock_category("sync_state"):
            sync_file = self.root / "sync_state.json"
            if sync_file.is_file():
                raw = json.loads(sync_file.read_text(encoding="utf-8"))
                if not PersonalStoreCrypto.is_encrypted_envelope(raw):
                    self._atomic_write_encrypted(sync_file, raw, "sync_state", "sync_state")
                    counts["sync_state"] += 1

        # 7. Messages
        with self.lock_category("messages"):
            for m_path in self.messages_dir.glob("*.json"):
                try:
                    raw = json.loads(m_path.read_text(encoding="utf-8"))
                    if not PersonalStoreCrypto.is_encrypted_envelope(raw):
                        acc_id = raw.get("account_id", "global")
                        self._atomic_write_encrypted(
                            m_path, raw, "message", m_path.stem, account_id=acc_id
                        )
                        counts["messages"] += 1
                except Exception as ex:
                    logger.warning("Failed migrating message %s: %s", m_path.name, ex)

        self.enforce_encryption()
        logger.info("Migrated storage to encrypted format: %s", counts)
        return counts

    # ── Data Purge & Wipe ────────────────────────────────────────────────────

    def delete_provider_data(self, provider: ProviderType) -> int:
        """Purge all cached messages, events, and tasks for a given provider."""
        deleted_count = 0

        # 1. Accounts
        acc_ids_to_del = [a.account_id for a in self.list_accounts(provider)]
        for aid in acc_ids_to_del:
            if self.delete_account(aid):
                deleted_count += 1

        # 2. Messages
        with self.lock_category("messages"), self._lock:
            for mid in [m.message_id for m in self._messages.values() if m.provider == provider]:
                del self._messages[mid]
                (self.messages_dir / f"{mid}.json").unlink(missing_ok=True)
                deleted_count += 1

        # 3. Tasks
        def _task_purge(tasks: dict[str, PersonalTask]) -> None:
            nonlocal deleted_count
            for tid in [tid for tid, t in tasks.items() if t.source_provider == provider]:
                del tasks[tid]
                deleted_count += 1

        self.mutate_tasks(_task_purge)

        # 4. Events
        def _evt_purge(evts: dict[str, PersonalEvent]) -> None:
            nonlocal deleted_count
            for eid in [eid for eid, e in evts.items() if e.provider == provider]:
                del evts[eid]
                deleted_count += 1

        self.mutate_events(_evt_purge)

        return deleted_count

    def wipe_all_data(self, purge_master_key: bool = False) -> None:
        """Complete local data purge deleting all cached entities and lockfiles."""
        with self._lock:
            self._accounts.clear()
            self._tasks.clear()
            self._events.clear()
            self._commitments.clear()
            self._contacts.clear()
            self._messages.clear()
            self._conversations.clear()
            self._sync_state = {"accounts": {}}

            # Clean all directory contents
            for item in self.root.glob("*"):
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)

            self.messages_dir.mkdir(parents=True, exist_ok=True)
            self.locks_dir.mkdir(parents=True, exist_ok=True)

            if purge_master_key:
                self._crypto.purge_master_key()
                logger.info("Purged PersonalStore master key.")

            logger.info("PersonalStore completely purged and wiped.")
