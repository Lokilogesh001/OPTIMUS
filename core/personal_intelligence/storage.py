"""core/personal_intelligence/storage.py — Local-First Atomic Persistent Store for Personal Intelligence.

Implements:
- Thread-safe multi-category storage under `memory/personal/`.
- Atomic JSON file writing via tempfile replace with exponential backoff on Windows.
- Multi-tenant stream cursor management in `sync_state.json` (resolving HIGH-03).
- In-memory indices for sub-millisecond retrieval (resolving HIGH-04).
- Idempotent deduplication check via `has_message` (resolving MED-03).
- Complete provider data wipe and total storage purge routines.
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
from pathlib import Path
from typing import Any

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


def mask_pii_string(text: str) -> str:
    """Mask email addresses and sensitive identifiers in log outputs (resolving LOW-01)."""
    if not text:
        return ""
    # Mask email patterns: j***@domain.com
    return re.sub(
        r"([a-zA-Z0-9_.+-])[a-zA-Z0-9_.+-]*@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
        r"\1***@\2",
        text,
    )


def _atomic_write_json(target_path: Path, data: Any, max_retries: int = 5) -> None:
    """Write data to target_path atomically using a per-write temp file with Windows backoff."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f"{target_path.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
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
    """Thread-safe, local-first persistent data repository for all personal intelligence entities."""

    _instance: PersonalStore | None = None
    _singleton_lock = threading.Lock()

    def __init__(self, root_dir: Path | str | None = None) -> None:
        if root_dir:
            self.root = Path(root_dir)
        else:
            self.root = Path.cwd() / "memory" / "personal"

        self.root.mkdir(parents=True, exist_ok=True)
        self.messages_dir = self.root / "messages"
        self.messages_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()

        # In-memory entity registries
        self._accounts: dict[str, Account] = {}
        self._tasks: dict[str, PersonalTask] = {}
        self._events: dict[str, PersonalEvent] = {}
        self._commitments: dict[str, Commitment] = {}
        self._contacts: dict[str, PersonContact] = {}
        self._messages: dict[str, Message] = {}
        self._conversations: dict[str, Conversation] = {}
        self._sync_state: dict[str, Any] = {"accounts": {}}

        self._load_all()

    @classmethod
    def instance(cls, root_dir: Path | str | None = None) -> PersonalStore:
        """Singleton accessor."""
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(root_dir)
            return cls._instance

    # ── Hydration from Disk ───────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Hydrate in-memory registry from disk upon initialization."""
        with self._lock:
            # 1. Accounts
            acc_file = self.root / "accounts.json"
            if acc_file.is_file():
                try:
                    raw = json.loads(acc_file.read_text(encoding="utf-8"))
                    for item in raw:
                        acc = Account.from_dict(item)
                        self._accounts[acc.account_id] = acc
                except Exception as ex:
                    logger.warning("Failed loading accounts: %s", ex)

            # 2. Tasks
            task_file = self.root / "tasks.json"
            if task_file.is_file():
                try:
                    raw = json.loads(task_file.read_text(encoding="utf-8"))
                    for item in raw:
                        t = PersonalTask.from_dict(item)
                        self._tasks[t.task_id] = t
                except Exception as ex:
                    logger.warning("Failed loading tasks: %s", ex)

            # 3. Events
            evt_file = self.root / "events.json"
            if evt_file.is_file():
                try:
                    raw = json.loads(evt_file.read_text(encoding="utf-8"))
                    for item in raw:
                        e = PersonalEvent.from_dict(item)
                        self._events[e.event_id] = e
                except Exception as ex:
                    logger.warning("Failed loading events: %s", ex)

            # 4. Commitments
            com_file = self.root / "commitments.json"
            if com_file.is_file():
                try:
                    raw = json.loads(com_file.read_text(encoding="utf-8"))
                    for item in raw:
                        c = Commitment.from_dict(item)
                        self._commitments[c.commitment_id] = c
                except Exception as ex:
                    logger.warning("Failed loading commitments: %s", ex)

            # 5. Contacts
            con_file = self.root / "contacts.json"
            if con_file.is_file():
                try:
                    raw = json.loads(con_file.read_text(encoding="utf-8"))
                    for item in raw:
                        con = PersonContact.from_dict(item)
                        self._contacts[con.contact_id] = con
                except Exception as ex:
                    logger.warning("Failed loading contacts: %s", ex)

            # 6. Sync State
            sync_file = self.root / "sync_state.json"
            if sync_file.is_file():
                try:
                    self._sync_state = json.loads(sync_file.read_text(encoding="utf-8"))
                except Exception as ex:
                    logger.warning("Failed loading sync_state: %s", ex)
                    self._sync_state = {"accounts": {}}

            # 7. Messages
            for m_path in self.messages_dir.glob("*.json"):
                try:
                    raw = json.loads(m_path.read_text(encoding="utf-8"))
                    msg = Message.from_dict(raw)
                    self._messages[msg.message_id] = msg
                except Exception as ex:
                    logger.debug("Failed loading message %s: %s", m_path.name, ex)

    # ── Accounts ─────────────────────────────────────────────────────────────

    def save_account(self, account: Account) -> None:
        """Persist or update an account record."""
        with self._lock:
            self._accounts[account.account_id] = account
            data = [acc.to_dict() for acc in self._accounts.values()]
            _atomic_write_json(self.root / "accounts.json", data)

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
        with self._lock:
            if account_id in self._accounts:
                del self._accounts[account_id]
                data = [acc.to_dict() for acc in self._accounts.values()]
                _atomic_write_json(self.root / "accounts.json", data)
                # Purge cursor
                if "accounts" in self._sync_state and account_id in self._sync_state["accounts"]:
                    del self._sync_state["accounts"][account_id]
                    _atomic_write_json(self.root / "sync_state.json", self._sync_state)
                return True
            return False

    # ── Sync Cursors (Multi-Tenant, resolving HIGH-03) ────────────────────────

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
        """Atomically update multi-tenant stream cursor without touching accounts.json (resolving LOW-02)."""
        with self._lock:
            if "accounts" not in self._sync_state:
                self._sync_state["accounts"] = {}
            if account_id not in self._sync_state["accounts"]:
                self._sync_state["accounts"][account_id] = {"streams": {}}
            if "streams" not in self._sync_state["accounts"][account_id]:
                self._sync_state["accounts"][account_id]["streams"] = {}

            self._sync_state["accounts"][account_id]["streams"][stream_key] = {
                "cursor": cursor,
                "cursor_type": cursor_type,
                "last_synced_at": time.time(),
                "status": status,
                "metadata": metadata or {},
            }
            _atomic_write_json(self.root / "sync_state.json", self._sync_state)

    def get_sync_cursor(self, account_id: str, stream_key: str) -> str | None:
        """Convenience method returning raw string cursor."""
        info = self.get_stream_cursor(account_id, stream_key)
        if info and isinstance(info, dict):
            return info.get("cursor")
        return None

    def set_sync_cursor(self, account_id: str, stream_key: str, cursor: str, cursor_type: str = "historyId") -> None:
        """Convenience method setting raw string cursor."""
        self.update_stream_cursor(account_id, stream_key, cursor, cursor_type=cursor_type)

    def get_accounts(self, *args, **kwargs) -> list[Account]:
        return self.list_accounts(*args, **kwargs)

    def get_messages(self, *args, **kwargs) -> list[Message]:
        return self.list_messages(*args, **kwargs)

    def get_tasks(self, *args, **kwargs) -> list[PersonalTask]:
        return self.list_tasks(*args, **kwargs)

    def get_events(self, *args, **kwargs) -> list[PersonalEvent]:
        return self.list_events(*args, **kwargs)

    def save_messages(self, messages: list[Message]) -> None:
        for m in messages:
            self.save_message(m)

    def save_tasks(self, tasks: list[PersonalTask]) -> None:
        for t in tasks:
            self.save_task(t)

    def save_events(self, events: list[PersonalEvent]) -> None:
        for e in events:
            self.save_event(e)

    # ── Messages ─────────────────────────────────────────────────────────────

    def save_message(self, message: Message) -> None:
        """Persist normalized message record."""
        with self._lock:
            self._messages[message.message_id] = message
            msg_path = self.messages_dir / f"{message.message_id}.json"
            _atomic_write_json(msg_path, message.to_dict())

    def has_message(self, message_id: str) -> bool:
        """Check if message exists in memory or on disk (idempotency check for MED-03)."""
        with self._lock:
            if message_id in self._messages:
                return True
            return (self.messages_dir / f"{message_id}.json").exists()

    def get_message(self, message_id: str) -> Message | None:
        with self._lock:
            return self._messages.get(message_id)

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
            # Sort newest first
            msgs.sort(key=lambda m: m.timestamp, reverse=True)
            return msgs[:limit]

    # ── Tasks ────────────────────────────────────────────────────────────────

    def save_task(self, task: PersonalTask) -> None:
        """Persist or update extracted task."""
        with self._lock:
            self._tasks[task.task_id] = task
            data = [t.to_dict() for t in self._tasks.values()]
            _atomic_write_json(self.root / "tasks.json", data)

    def get_task(self, task_id: str) -> PersonalTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def find_duplicate_task(self, dedup_key: str) -> PersonalTask | None:
        """Find active task matching dedup_key; completed/cancelled tasks ignored (resolving MED-01)."""
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
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                data = [t.to_dict() for t in self._tasks.values()]
                _atomic_write_json(self.root / "tasks.json", data)
                return True
            return False

    # ── Events ───────────────────────────────────────────────────────────────

    def save_event(self, event: PersonalEvent) -> None:
        """Persist or update calendar event."""
        with self._lock:
            self._events[event.event_id] = event
            data = [e.to_dict() for e in self._events.values()]
            _atomic_write_json(self.root / "events.json", data)

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
        with self._lock:
            if event_id in self._events:
                del self._events[event_id]
                data = [e.to_dict() for e in self._events.values()]
                _atomic_write_json(self.root / "events.json", data)
                return True
            return False

    # ── Commitments ──────────────────────────────────────────────────────────

    def save_commitment(self, commitment: Commitment) -> None:
        with self._lock:
            self._commitments[commitment.commitment_id] = commitment
            data = [c.to_dict() for c in self._commitments.values()]
            _atomic_write_json(self.root / "commitments.json", data)

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
        with self._lock:
            self._contacts[contact.contact_id] = contact
            data = [c.to_dict() for c in self._contacts.values()]
            _atomic_write_json(self.root / "contacts.json", data)

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

    # ── Data Purge & Wipe ────────────────────────────────────────────────────

    def delete_provider_data(self, provider: ProviderType) -> int:
        """Purge all cached messages, events, and tasks for a given provider."""
        with self._lock:
            deleted_count = 0
            # 1. Accounts
            for aid in [a.account_id for a in self._accounts.values() if a.provider == provider]:
                self.delete_account(aid)
                deleted_count += 1

            # 2. Messages
            for mid in [m.message_id for m in self._messages.values() if m.provider == provider]:
                del self._messages[mid]
                (self.messages_dir / f"{mid}.json").unlink(missing_ok=True)
                deleted_count += 1

            # 3. Tasks
            t_ids = [t.task_id for t in self._tasks.values() if t.source_provider == provider]
            for tid in t_ids:
                del self._tasks[tid]
                deleted_count += 1
            if t_ids:
                _atomic_write_json(self.root / "tasks.json", [t.to_dict() for t in self._tasks.values()])

            # 4. Events
            e_ids = [e.event_id for e in self._events.values() if e.provider == provider]
            for eid in e_ids:
                del self._events[eid]
                deleted_count += 1
            if e_ids:
                _atomic_write_json(self.root / "events.json", [e.to_dict() for e in self._events.values()])

            return deleted_count

    def wipe_all_data(self) -> None:
        """Complete local data purge deleting all cached personal intelligence entities."""
        with self._lock:
            self._accounts.clear()
            self._tasks.clear()
            self._events.clear()
            self._commitments.clear()
            self._contacts.clear()
            self._messages.clear()
            self._conversations.clear()
            self._sync_state = {"accounts": {}}

            # Clean directory
            for item in self.root.glob("*"):
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
            self.messages_dir.mkdir(parents=True, exist_ok=True)
            logger.info("PersonalStore completely purged and wiped.")
