"""core/personal_intelligence/engine.py — Unified Personal Intelligence & Task Extraction Engine.

Implements:
- Heuristic and pattern-based task, deadline, and commitment extraction.
- Deterministic date/timezone normalization.
- Conversation-anchored task deduplication with completed-task exemption (resolving MED-01).
- Multi-factor message importance scoring and triage.
- Calendar overlap and schedule conflict detection.
- Fault-isolated multi-provider background synchronization via SyncCoordinator.
- Integration with IntelligentScheduler, MemoryStore, and NotificationManager.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import hashlib
import logging
import re
import time
from typing import Any

from core.personal_intelligence.providers.base import BaseProviderAdapter
from core.personal_intelligence.providers.gmail import GmailAdapter
from core.personal_intelligence.providers.google_calendar import GoogleCalendarAdapter
from core.personal_intelligence.providers.outlook import OutlookAdapter
from core.personal_intelligence.providers.whatsapp_business import (
    WhatsAppBusinessAdapter,
)
from core.personal_intelligence.providers.whatsapp_personal import (
    WhatsAppPersonalAdapter,
)
from core.personal_intelligence.storage import PersonalStore, mask_pii_string
from core.personal_intelligence.types import (
    Account,
    AuthStatus,
    Commitment,
    CommitmentStatus,
    Message,
    PersonalEvent,
    PersonalTask,
    ProviderType,
    SourceReference,
    SyncStatus,
    TaskPriority,
    TaskStatus,
)

logger = logging.getLogger("optimus.personal.engine")

# ── Extraction Pattern Regexes ────────────────────────────────────────────────

_TASK_REQUEST_PATTERNS = [
    re.compile(r"(?:please|kindly|could you|can you|need you to|make sure to)\s+([^.!?\n]{5,120})", re.IGNORECASE),
    re.compile(r"(?:action required|to-do|todo):\s*([^.!?\n]{5,120})", re.IGNORECASE),
    re.compile(r"(?:remember to|don't forget to|ensure)\s+([^.!?\n]{5,120})", re.IGNORECASE),
]

_USER_COMMITMENT_PATTERNS = [
    re.compile(r"(?:I will|I'll|I promise to|I can send|I will provide)\s+([^.!?\n]{5,120})", re.IGNORECASE),
    re.compile(r"(?:will have it ready|will follow up|will update you)\s*(?:by|before)?\s*([^.!?\n]{0,80})", re.IGNORECASE),
]

_EXPLICIT_DEADLINE_PATTERNS = [
    re.compile(r"(?:by|before|due on|deadline is)\s+([A-Za-z]+ \d{1,2}(?:st|nd|rd|th)?(?:, \d{4})?)", re.IGNORECASE),
    re.compile(r"(?:by|before|due on|deadline is)\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    re.compile(r"(?:by|before)\s+(tomorrow|next Monday|next Tuesday|next Wednesday|next Thursday|next Friday|end of day|eod)", re.IGNORECASE),
    re.compile(r"(?:by|at)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))", re.IGNORECASE),
]

_URGENT_KEYWORDS = {
    "urgent", "asap", "emergency", "deadline", "critical", "immediately",
    "action required", "priority", "important", "time sensitive", "past due"
}


class PersonalIntelligenceEngine:
    """Core intelligence engine transforming raw communications into actionable personal life entities."""

    def __init__(self, store: PersonalStore | None = None) -> None:
        self.store = store or PersonalStore.instance()
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="PersonalSync")

    # ── Task & Commitment Extraction ──────────────────────────────────────────

    def extract_tasks_and_commitments_from_message(self, message: Message) -> tuple[list[PersonalTask], list[Commitment]]:
        """Analyze message body and metadata to deterministically extract tasks and commitments."""
        text = f"{message.subject}\n{message.body_snippet}"
        tasks: list[PersonalTask] = []
        commitments: list[Commitment] = []

        # 1. Parse Deadlines
        detected_deadline: float | None = None
        is_inferred = True

        for pat in _EXPLICIT_DEADLINE_PATTERNS:
            match = pat.search(text)
            if match:
                raw_date = match.group(1).strip()
                parsed_ts = self._parse_date_string(raw_date)
                if parsed_ts:
                    detected_deadline = parsed_ts
                    is_inferred = False
                    break

        # If no explicit deadline but text contains "urgent" or "asap", infer 24-hour deadline
        if detected_deadline is None and any(w in text.lower() for w in ("asap", "urgently", "immediately")):
            detected_deadline = message.timestamp + 86400.0
            is_inferred = True

        # 2. Extract Tasks (Obligations requested of user)
        seen_titles_in_msg: set[str] = set()
        for pat in _TASK_REQUEST_PATTERNS:
            for match in pat.finditer(text):
                action_text = match.group(1).strip()
                if len(action_text) < 5 or self._is_boilerplate(action_text):
                    continue

                # Normalize title
                clean_title = action_text.capitalize()
                if clean_title.endswith((".", ",", ";")):
                    clean_title = clean_title[:-1].strip()

                # Generate conversation-anchored dedup key (resolving MED-01)
                norm_title = re.sub(r"[^a-zA-Z0-9]", "", clean_title.lower())
                # Intra-message dedup: if title or sub-phrase already processed in this message
                if any(norm_title in st or st in norm_title for st in seen_titles_in_msg):
                    continue
                seen_titles_in_msg.add(norm_title)

                counterpart = message.sender.lower()
                conv_id = message.conversation_id or message.message_id
                dedup_key = hashlib.sha256(f"{norm_title}:{counterpart}:{conv_id}".encode()).hexdigest()[:16]

                priority = TaskPriority.HIGH if any(w in clean_title.lower() for w in _URGENT_KEYWORDS) else TaskPriority.MEDIUM

                s_ref = SourceReference(
                    provider=message.provider,
                    account_id=message.account_id,
                    record_id=message.message_id,
                    uri=f"{message.provider.value.lower()}://{message.message_id}",
                    snippet=match.group(0),
                    timestamp=message.timestamp,
                )

                # Check if existing active task matches (resolving duplicate obligations)
                existing = self.store.find_duplicate_task(dedup_key)
                if existing:
                    # Update evidence without duplicating
                    if s_ref.snippet not in existing.evidence:
                        existing.evidence.append(s_ref.snippet)
                        self.store.save_task(existing)
                    continue

                task = PersonalTask(
                    title=clean_title,
                    description=f"Extracted from {message.provider.value} message from {message.sender_name or message.sender}.",
                    source_provider=message.provider,
                    source_ref=s_ref,
                    created_at=time.time(),
                    due_date=detected_deadline,
                    timezone="UTC",
                    confidence=0.90 if not is_inferred else 0.75,
                    status=TaskStatus.PENDING,
                    priority=priority,
                    is_deadline_inferred=is_inferred,
                    evidence=[match.group(0)],
                    counterpart=message.sender,
                    conversation_id=message.conversation_id,
                    dedup_key=dedup_key,
                )
                tasks.append(task)
                self.store.save_task(task)

        # 3. Extract User Commitments ("I will...")
        for pat in _USER_COMMITMENT_PATTERNS:
            for match in pat.finditer(text):
                promise_text = match.group(1).strip()
                if len(promise_text) < 5 or self._is_boilerplate(promise_text):
                    continue

                clean_promise = f"I will {promise_text}".capitalize()
                s_ref = SourceReference(
                    provider=message.provider,
                    account_id=message.account_id,
                    record_id=message.message_id,
                    uri=f"{message.provider.value.lower()}://{message.message_id}",
                    snippet=match.group(0),
                    timestamp=message.timestamp,
                )

                commitment = Commitment(
                    title=clean_promise,
                    counterpart=message.sender_name or message.sender,
                    counterpart_email=message.sender,
                    is_user_promise=True,
                    deadline=detected_deadline,
                    status=CommitmentStatus.OPEN,
                    source_ref=s_ref,
                    created_at=time.time(),
                )
                commitments.append(commitment)
                self.store.save_commitment(commitment)

        return tasks, commitments

    def _parse_date_string(self, raw_str: str) -> float | None:
        """Helper normalizing date phrases to Unix timestamp."""
        raw_low = raw_str.lower().strip()
        now = datetime.datetime.now(datetime.UTC)

        if "tomorrow" in raw_low:
            return (now + datetime.timedelta(days=1)).replace(hour=17, minute=0, second=0).timestamp()
        if "next monday" in raw_low:
            days_ahead = (0 - now.weekday() + 7) % 7 or 7
            return (now + datetime.timedelta(days=days_ahead)).replace(hour=17, minute=0, second=0).timestamp()
        if "next friday" in raw_low:
            days_ahead = (4 - now.weekday() + 7) % 7 or 7
            return (now + datetime.timedelta(days=days_ahead)).replace(hour=17, minute=0, second=0).timestamp()
        if raw_low in ("end of day", "eod"):
            return now.replace(hour=18, minute=0, second=0).timestamp()

        # Try ISO date YYYY-MM-DD
        try:
            dt = datetime.datetime.fromisoformat(raw_str)
            return dt.replace(tzinfo=datetime.UTC).timestamp()
        except Exception:
            pass

        return None

    def _is_boilerplate(self, text: str) -> bool:
        """Filter out common unsubscribe or footer phrases."""
        low = text.lower()
        return any(
            bp in low
            for bp in (
                "unsubscribe", "click here", "privacy policy", "terms of service",
                "all rights reserved", "do not reply to this email"
            )
        )

    # ── Message Triage & Importance Scoring ───────────────────────────────────

    def score_message_importance(self, message: Message) -> float:
        """Compute an importance score between 0.0 and 1.0."""
        score = 0.20  # Baseline

        # VIP or known frequent contact
        contact = self.store.get_contact_by_email(message.sender)
        if contact:
            if contact.is_vip:
                score += 0.35
            elif contact.interaction_count > 5:
                score += 0.15

        # Provider important flag
        if message.is_important or "IMPORTANT" in message.labels or "STARRED" in message.labels:
            score += 0.25

        # Urgent text cues
        content = f"{message.subject} {message.body_snippet}".lower()
        if any(w in content for w in _URGENT_KEYWORDS):
            score += 0.25

        # Action requests or obligations requested
        if any(pat.search(content) for pat in _TASK_REQUEST_PATTERNS):
            score += 0.20

        # 1-on-1 vs group broadcast
        if len(message.recipients) == 1:
            score += 0.10

        score = min(max(score, 0.0), 1.0)
        message.importance_score = round(score, 2)
        message.is_important = score >= 0.70

        # Suggested actions
        if score >= 0.70:
            if "reply" not in message.suggested_actions:
                message.suggested_actions.append("Draft Reply")
            if "calendar" not in message.suggested_actions and any(w in content for w in ("meeting", "call", "schedule")):
                message.suggested_actions.append("Schedule Event")

        return message.importance_score

    # ── Calendar Conflict Detection ───────────────────────────────────────────

    def detect_schedule_conflicts(self, target_event: PersonalEvent) -> list[PersonalEvent]:
        """Detect any overlapping confirmed events across all connected accounts."""
        existing = self.store.list_events(start_min=target_event.start_time - 86400, start_max=target_event.end_time + 86400)
        conflicts = []
        for ev in existing:
            if ev.event_id == target_event.event_id or not ev.is_confirmed:
                continue
            # Overlap check
            if target_event.start_time < ev.end_time and target_event.end_time > ev.start_time:
                conflicts.append(ev)
        return conflicts

    # ── Multi-Provider Synchronization (Fault Isolated) ──────────────────────

    def get_adapter_for_account(self, account: Account, http_client: Any | None = None) -> BaseProviderAdapter:
        """Instantiate corresponding adapter instance for an account."""
        if account.provider == ProviderType.GMAIL:
            return GmailAdapter(account, http_client=http_client)
        elif account.provider == ProviderType.GOOGLE_CALENDAR:
            return GoogleCalendarAdapter(account, http_client=http_client)
        elif account.provider == ProviderType.OUTLOOK:
            return OutlookAdapter(account, http_client=http_client)
        elif account.provider == ProviderType.WHATSAPP_BUSINESS:
            return WhatsAppBusinessAdapter(account, http_client=http_client)
        elif account.provider == ProviderType.WHATSAPP_PERSONAL:
            return WhatsAppPersonalAdapter(account)
        raise ValueError(f"Unsupported provider: {account.provider}")

    def sync_account(self, account: Account, http_client: Any | None = None) -> dict[str, Any]:
        """Execute synchronization for a single account with cursor tracking and error isolation."""
        adapter = self.get_adapter_for_account(account, http_client=http_client)
        account.sync_status = SyncStatus.SYNCING
        self.store.save_account(account)

        synced_messages: list[Message] = []
        synced_events: list[PersonalEvent] = []

        try:
            # Check for existing stream cursor (resolving HIGH-03)
            stream_key = "mail" if account.provider in (ProviderType.GMAIL, ProviderType.OUTLOOK) else "calendar:primary"
            cursor_data = self.store.get_stream_cursor(account.account_id, stream_key)
            cursor = cursor_data.get("cursor") if cursor_data else None

            if cursor:
                msgs, evts, new_cursor = adapter.incremental_sync(cursor)
            else:
                msgs, evts, new_cursor = adapter.initial_sync()

            # Process Messages
            for m in msgs:
                self.score_message_importance(m)
                self.store.save_message(m)
                self.extract_tasks_and_commitments_from_message(m)
                synced_messages.append(m)

            # Process Events
            for e in evts:
                # Check conflicts
                conflicts = self.detect_schedule_conflicts(e)
                if conflicts:
                    e.conflict_detected = True
                self.store.save_event(e)
                synced_events.append(e)

            # Update stream cursor in sync_state.json
            if new_cursor:
                self.store.update_stream_cursor(
                    account.account_id,
                    stream_key=stream_key,
                    cursor=new_cursor,
                    status="SUCCESS",
                )

            account.sync_status = SyncStatus.SUCCESS
            account.last_synced_at = time.time()
            account.error_message = ""
            self.store.save_account(account)

            logger.info(
                "Sync succeeded for account %s (%s): %d messages, %d events.",
                account.account_id, mask_pii_string(account.email_or_id), len(synced_messages), len(synced_events)
            )
            return {
                "success": True,
                "account_id": account.account_id,
                "provider": account.provider.value,
                "messages_count": len(synced_messages),
                "events_count": len(synced_events),
            }

        except Exception as ex:
            account.sync_status = SyncStatus.FAILED
            account.error_message = str(ex)
            self.store.save_account(account)
            logger.error("Sync failed for account %s: %s", account.account_id, ex)
            return {
                "success": False,
                "account_id": account.account_id,
                "provider": account.provider.value,
                "error": str(ex),
            }

    def sync_all_connected_accounts(self, http_client: Any | None = None) -> list[dict[str, Any]]:
        """Synchronize all connected accounts concurrently with worker thread isolation."""
        accounts = [a for a in self.store.list_accounts() if a.auth_status == AuthStatus.CONNECTED]
        if not accounts:
            return []

        results: list[dict[str, Any]] = []
        futures = {self._thread_pool.submit(self.sync_account, acc, http_client): acc for acc in accounts}
        for fut in concurrent.futures.as_completed(futures):
            try:
                res = fut.result()
                results.append(res)
            except Exception as ex:
                results.append({"success": False, "error": str(ex)})

        return results
