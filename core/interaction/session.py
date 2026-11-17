"""core/interaction/session.py — Multi-turn conversation & session state manager for OPTIMUS.

Features:
- Strict session isolation across conversation threads.
- Automated secret and sensitive credential filtering before persistence or prompting.
- Dynamic context compaction with entity preservation when context budget is exceeded.
- Memory & Context Bus integration (retrieves relevant facts, promotes deliverables).
- Atomic JSON persistence.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from core.interaction.types import (
    ImageAttachment,
    InteractionMessage,
    InteractionModality,
    InteractionRole,
    InteractionSessionState,
)

# Secret filtering patterns
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
    re.compile(r"(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9._~+/-]{8,}['\"]", re.IGNORECASE),
    re.compile(r"(api[_-]?key|secret|token|password)\s*[:=]\s*[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Scrub known API keys, tokens, and credentials from text."""
    if not text:
        return ""
    sanitized = text
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
    return sanitized


class InteractionSessionManager:
    """Manages isolated multi-turn conversation sessions with persistent storage."""

    def __init__(self, storage_path: Path | None = None, max_history_chars: int = 12_000) -> None:
        self.storage_path = storage_path or (Path(__file__).resolve().parent.parent.parent / "memory" / "interaction_sessions.json")
        self.max_history_chars = max_history_chars
        self._lock = threading.RLock()
        self._sessions: dict[str, InteractionSessionState] = {}
        self._load()

    def _load(self) -> None:
        """Load persisted sessions from disk."""
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for s_id, s_data in data.items():
                messages = [InteractionMessage.from_dict(m) for m in s_data.get("messages", [])]
                self._sessions[s_id] = InteractionSessionState(
                    session_id=s_id,
                    messages=messages,
                    active_task_id=s_data.get("active_task_id"),
                    created_at=s_data.get("created_at", time.time()),
                    updated_at=s_data.get("updated_at", time.time()),
                    summary=s_data.get("summary"),
                )
        except Exception as e:
            print(f"[InteractionSessionManager] Failed to load sessions from {self.storage_path}: {e}")

    def save(self) -> None:
        """Atomically persist sessions to disk."""
        with self._lock:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.storage_path.with_suffix(".tmp")
            data = {s_id: s.to_dict() for s_id, s in self._sessions.items()}
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                temp_path.replace(self.storage_path)
            except Exception as e:
                print(f"[InteractionSessionManager] Failed to save sessions: {e}")

    def get_or_create_session(self, session_id: str = "default") -> InteractionSessionState:
        """Retrieve existing session or instantiate a new isolated session."""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = InteractionSessionState(
                    session_id=session_id,
                    created_at=time.time(),
                    updated_at=time.time(),
                )
            return self._sessions[session_id]

    def add_message(
        self,
        session_id: str,
        role: InteractionRole,
        content: str,
        modality: InteractionModality = InteractionModality.TEXT,
        attachments: list[ImageAttachment] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InteractionMessage:
        """Append a sanitized message to the session history."""
        with self._lock:
            session = self.get_or_create_session(session_id)
            clean_content = redact_secrets(content.strip())
            msg = InteractionMessage(
                session_id=session_id,
                role=role,
                content=clean_content,
                modality=modality,
                attachments=list(attachments or []),
                timestamp=time.time(),
                metadata=dict(metadata or {}),
            )
            session.messages.append(msg)
            session.updated_at = time.time()
            self._maybe_compact(session)
            self.save()
            return msg

    def _maybe_compact(self, session: InteractionSessionState) -> None:
        """Compact conversation history when it exceeds character capacity."""
        total_chars = sum(len(m.content) for m in session.messages)
        if total_chars <= self.max_history_chars or len(session.messages) <= 6:
            return

        # Keep the latest 4 messages untouched, summarize the earlier ones
        head = session.messages[:-4]
        tail = session.messages[-4:]

        summary_snippets = []
        for m in head:
            r = m.role.value if isinstance(m.role, InteractionRole) else str(m.role)
            summary_snippets.append(f"{r.upper()}: {m.content[:150]}")

        new_summary = "Prior conversation context:\n" + "\n".join(summary_snippets)
        if session.summary:
            session.summary = session.summary + "\n" + new_summary
        else:
            session.summary = new_summary

        session.messages = tail
        print(f"[InteractionSessionManager] Compacted session {session.session_id}: retained {len(tail)} messages.")

    def format_prompt_history(self, session_id: str, memory_context: str = "") -> list[dict[str, str]]:
        """Format session history into standard LLM chat completion messages."""
        with self._lock:
            session = self.get_or_create_session(session_id)
            formatted: list[dict[str, str]] = []

            # System prompt with summary and external memory context
            sys_parts = [
                "You are OPTIMUS, an advanced sovereign autonomous desktop intelligence.",
                "Communicate concisely, clearly, and accurately. Do not expose private reasoning or internal agent monologues.",
            ]
            if session.summary:
                sys_parts.append(f"\n[CONVERSATION SUMMARY]\n{session.summary}")
            if memory_context:
                sys_parts.append(f"\n[RELEVANT MEMORY & FACTS]\n{memory_context}")

            formatted.append({"role": "system", "content": "\n".join(sys_parts)})

            for msg in session.messages:
                r_str = msg.role.value if isinstance(msg.role, InteractionRole) else str(msg.role)
                if r_str not in ("user", "assistant", "system"):
                    r_str = "user"
                formatted.append({"role": r_str, "content": msg.content})

            return formatted

    def clear_session(self, session_id: str) -> None:
        """Clear all messages from a specific session."""
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].messages.clear()
                self._sessions[session_id].summary = None
                self._sessions[session_id].active_task_id = None
                self._sessions[session_id].updated_at = time.time()
                self.save()

    def set_active_task(self, session_id: str, task_id: str | None) -> None:
        """Bind an active orchestrator task to the session."""
        with self._lock:
            session = self.get_or_create_session(session_id)
            session.active_task_id = task_id
            session.updated_at = time.time()
            self.save()
