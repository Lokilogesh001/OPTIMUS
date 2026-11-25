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
import email.utils
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


def parse_retry_after(header_val: str | None) -> float:
    """Parse HTTP Retry-After header as seconds or delta from HTTP date (RFC 7231)."""
    if not header_val:
        return 0.0
    val = str(header_val).strip()
    try:
        return max(0.0, float(val))
    except ValueError:
        pass
    try:
        parsed_tuple = email.utils.parsedate_tz(val)
        if parsed_tuple:
            target_ts = email.utils.mktime_tz(parsed_tuple)
            return max(0.0, target_ts - time.time())
    except Exception:
        pass
    return 0.0


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
        seen_promises_in_msg: set[str] = set()
        for pat in _USER_COMMITMENT_PATTERNS:
            for match in pat.finditer(text):
                promise_text = match.group(1).strip()
                if len(promise_text) < 5 or self._is_boilerplate(promise_text):
                    continue

                clean_promise = f"I will {promise_text}".capitalize()
                norm_promise = re.sub(r"[^a-zA-Z0-9]", "", clean_promise.lower())
                if any(norm_promise in sp or sp in norm_promise for sp in seen_promises_in_msg):
                    continue
                seen_promises_in_msg.add(norm_promise)

                counterpart = message.sender.lower()
                conv_id = message.conversation_id or message.message_id
                dedup_key = hashlib.sha256(f"com:{norm_promise}:{counterpart}:{conv_id}".encode()).hexdigest()[:16]

                # Check if duplicate commitment exists
                existing_c = self.store.find_duplicate_commitment(dedup_key)
                if existing_c:
                    continue

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
                    dedup_key=dedup_key,
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

    def disconnect_account(self, account_id: str, http_client: Any | None = None) -> bool:
        """Disconnect and revoke an account, purging credentials honestly."""
        account = self.store.get_account(account_id)
        if not account:
            logger.warning("Account %s not found for disconnect.", account_id)
            return False

        try:
            adapter = self.get_adapter_for_account(account, http_client=http_client)
            adapter.revoke()
        except Exception as ex:  # noqa: BLE001
            logger.warning("Error during adapter revoke for %s: %s", account_id, ex)
            account.auth_status = AuthStatus.DISCONNECTED_LOCAL_ONLY

        self.store.save_account(account)
        logger.info("Account %s disconnected with status: %s", account_id, account.auth_status.value)
        return True

    # ── Integration Discovery & Cross-Service Intelligence (Phase 1) ──────────

    def discover_integrations(self) -> list[dict[str, Any]]:
        """Honest inspection of connected personal integrations, scopes, and operational state."""
        accounts = self.store.list_accounts()
        capabilities_by_provider: dict[ProviderType, tuple[list[str], list[str]]] = {
            ProviderType.GMAIL: (
                ["search_messages", "read_message", "summarize_emails", "draft_reply", "send_message"],
                ["send_message"],
            ),
            ProviderType.GOOGLE_CALENDAR: (
                ["list_events", "search_events", "inspect_event", "create_event", "update_event", "cancel_event"],
                ["create_event", "update_event", "cancel_event"],
            ),
            ProviderType.OUTLOOK: (
                ["search_messages", "list_events", "draft_reply", "send_message"],
                ["send_message"],
            ),
            ProviderType.WHATSAPP_BUSINESS: (
                ["search_contacts", "draft_message", "send_message"],
                ["send_message"],
            ),
            ProviderType.WHATSAPP_PERSONAL: (
                ["search_contacts", "draft_message", "send_message"],
                ["send_message"],
            ),
        }

        inventory: list[dict[str, Any]] = []
        seen_providers: set[ProviderType] = set()
        for acc in accounts:
            seen_providers.add(acc.provider)
            supported, approval_gated = capabilities_by_provider.get(acc.provider, ([], []))
            connected = acc.auth_status == AuthStatus.CONNECTED
            operational = connected and acc.sync_status != SyncStatus.FAILED
            inventory.append({
                "provider": acc.provider.value,
                "account_id": acc.account_id,
                "email_or_id": mask_pii_string(acc.email_or_id),
                "connected": connected,
                "authorized": connected,
                "operational": operational,
                "supported_actions": supported,
                "requires_approval": approval_gated,
                "last_synced_at": acc.last_synced_at,
                "error": acc.error_message or None,
            })

        for p_type, (supported, approval_gated) in capabilities_by_provider.items():
            if p_type not in seen_providers:
                inventory.append({
                    "provider": p_type.value,
                    "account_id": None,
                    "email_or_id": None,
                    "connected": False,
                    "authorized": False,
                    "operational": False,
                    "supported_actions": supported,
                    "requires_approval": approval_gated,
                    "last_synced_at": 0.0,
                    "error": "Not connected",
                })
        return inventory

    def prepare_for_next_meeting(self, now: float | None = None) -> dict[str, Any]:
        """Cross-service briefing combining next calendar event, related emails, and tasks."""
        cur_t = now if now is not None else time.time()
        events = [
            ev for ev in self.store.list_events(start_min=cur_t, start_max=cur_t + 7 * 86400)
            if ev.end_time >= cur_t
        ]
        events.sort(key=lambda e: e.start_time)
        if not events:
            cal_connected = any(
                a.provider == ProviderType.GOOGLE_CALENDAR and a.auth_status == AuthStatus.CONNECTED
                for a in self.store.list_accounts()
            )
            return {
                "found": False,
                "calendar_connected": cal_connected,
                "message": (
                    "No upcoming meetings found in the next 7 days."
                    if cal_connected
                    else "Google Calendar is not connected. Connect your calendar in Personal Intelligence to prepare meeting briefings."
                ),
            }

        nxt = events[0]
        keywords = [w.lower() for w in re.findall(r"[a-zA-Z0-9]{3,}", nxt.title or "")]
        attendees_lower = {a.lower() for a in (nxt.attendees or [])}

        # Contextually retrieve only matching messages (never dump entire inbox)
        from core.outcome import untrusted

        all_msgs = self.store.list_messages(limit=200)
        related_msgs: list[dict[str, Any]] = []
        for msg in all_msgs:
            sender_l = (msg.sender or "").lower()
            subj_l = (msg.subject or "").lower()
            body_l = (msg.body_snippet or "").lower()
            matched_attendee = any(att in sender_l for att in attendees_lower if att)
            matched_kw = any(kw in subj_l or kw in body_l for kw in keywords)
            if matched_attendee or matched_kw:
                raw_sender = re.sub(r"<<<(?:END[- ]?)?UNTRUSTED(?:-ESCAPED)?(?:\s+source=[^>]*)?>>>", "", str(msg.sender_name or msg.sender or "")).strip()
                raw_subject = re.sub(r"<<<(?:END[- ]?)?UNTRUSTED(?:-ESCAPED)?(?:\s+source=[^>]*)?>>>", "", str(msg.subject or "")).strip()
                raw_snippet = re.sub(r"<<<(?:END[- ]?)?UNTRUSTED(?:-ESCAPED)?(?:\s+source=[^>]*)?>>>", "", str(msg.body_snippet or "")).strip()[:180]
                related_msgs.append({
                    "message_id": msg.message_id,
                    "sender": untrusted("email", raw_sender),
                    "subject": untrusted("email", raw_subject),
                    "snippet": untrusted("email", raw_snippet),
                    "timestamp": msg.timestamp,
                })
                if len(related_msgs) >= 5:
                    break

        start_str = datetime.datetime.fromtimestamp(nxt.start_time).strftime("%Y-%m-%d %H:%M")
        lines = [
            f"Next Meeting: {nxt.title} ({start_str})",
            f"Location/Link: {nxt.location or 'Unspecified'}",
            f"Attendees: {', '.join(nxt.attendees) if nxt.attendees else 'None listed'}",
        ]
        if nxt.conflict_detected:
            lines.append("Warning: Schedule conflict detected for this time slot.")
        if related_msgs:
            lines.append("Related Emails (untrusted external content):")
            for rm in related_msgs:
                lines.append(f"  - [{rm['sender']}] {rm['subject']}: {rm['snippet']}")
        else:
            lines.append("Related Emails: None found matching meeting subject or attendees.")

        return {
            "found": True,
            "event": nxt.to_dict(),
            "related_messages": related_msgs,
            "briefing": "\n".join(lines),
        }

    def find_important_morning_emails(
        self,
        *,
        now: float | None = None,
        timezone_name: str | None = None,
        live_fetch: bool = True,
        limit: int = 10,
        http_client: Any | None = None,
    ) -> dict[str, Any]:
        """Check live Gmail status, establish local morning window, and rank important vs ordinary messages."""
        import os
        from zoneinfo import ZoneInfo
        from core.outcome import untrusted

        tz = None
        if timezone_name:
            try:
                tz = ZoneInfo(timezone_name)
            except Exception:
                tz = None
        if cur_t := now:
            now_dt = datetime.datetime.fromtimestamp(cur_t, tz=tz).astimezone(tz) if tz else datetime.datetime.fromtimestamp(cur_t).astimezone()
        else:
            now_dt = datetime.datetime.now(tz) if tz else datetime.datetime.now().astimezone()

        morning_start_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        noon_dt = now_dt.replace(hour=12, minute=0, second=0, microsecond=0)
        morning_end_dt = now_dt if now_dt < noon_dt else noon_dt
        morning_start_ts = morning_start_dt.timestamp()
        morning_end_ts = morning_end_dt.timestamp()

        accounts = [a for a in self.store.list_accounts() if a.provider == ProviderType.GMAIL]
        if not accounts:
            return {
                "connected": False,
                "retrieval_ok": False,
                "connection_error": "No Google Gmail account is connected in OPTIMUS.",
                "required_action": "Connect your Gmail account in Personal Intelligence settings.",
                "window_start": morning_start_dt.isoformat(),
                "window_end": morning_end_dt.isoformat(),
                "important_messages": [],
                "ordinary_messages": [],
                "summary": "Gmail is not connected. No Gmail account is configured in OPTIMUS.",
            }

        gmail_acc = accounts[0]
        mock_mode = os.environ.get("OPTIMUS_MOCK_NETWORK") == "1"
        messages: list[Message] = []
        source = "local_store"
        retrieval_error: str | None = None

        if live_fetch and not mock_mode:
            adapter = GmailAdapter(account=gmail_acc, http_client=http_client)
            health = adapter.check_health()
            if not health.get("healthy"):
                err = str(health.get("error") or f"Gmail auth status: {health.get('auth_status')}")
                if gmail_acc.auth_status.value != str(health.get("auth_status") or ""):
                    try:
                        gmail_acc.auth_status = AuthStatus(str(health["auth_status"]))
                        self.store.save_account(gmail_acc)
                    except Exception:
                        pass
                return {
                    "connected": False,
                    "retrieval_ok": False,
                    "account_email": mask_pii_string(gmail_acc.email_or_id),
                    "connection_error": err,
                    "required_action": "Re-authenticate Gmail OAuth in Personal Intelligence settings.",
                    "window_start": morning_start_dt.isoformat(),
                    "window_end": morning_end_dt.isoformat(),
                    "important_messages": [],
                    "ordinary_messages": [],
                    "summary": f"Gmail connection failed: {err}. Action required: re-authenticate Gmail OAuth.",
                }

            try:
                # Query strictly from start of local day minus safety margin, then exact window filter below
                after_epoch = int(morning_start_ts) - 60
                fetched = adapter.fetch_recent_messages(limit=25, query=f"after:{after_epoch}")
                self.store.save_messages(fetched)
                messages = fetched
                source = "live_gmail_api"
            except Exception as ex:
                retrieval_error = str(ex)
                return {
                    "connected": True,
                    "retrieval_ok": False,
                    "account_email": mask_pii_string(health.get("email") or gmail_acc.email_or_id),
                    "retrieval_error": retrieval_error,
                    "window_start": morning_start_dt.isoformat(),
                    "window_end": morning_end_dt.isoformat(),
                    "important_messages": [],
                    "ordinary_messages": [],
                    "summary": (
                        f"Gmail is connected ({mask_pii_string(gmail_acc.email_or_id)}), "
                        f"but message retrieval failed: {retrieval_error}"
                    ),
                }
        else:
            if gmail_acc.auth_status != AuthStatus.CONNECTED:
                return {
                    "connected": False,
                    "retrieval_ok": False,
                    "connection_error": f"Gmail account status is {gmail_acc.auth_status.value}.",
                    "required_action": "Reconnect Gmail in Personal Intelligence settings.",
                    "window_start": morning_start_dt.isoformat(),
                    "window_end": morning_end_dt.isoformat(),
                    "important_messages": [],
                    "ordinary_messages": [],
                    "summary": f"Gmail is not connected (status: {gmail_acc.auth_status.value}).",
                }
            messages = self.store.list_messages(provider=ProviderType.GMAIL, limit=50)

        # Filter to the local morning time window
        morning_msgs = [m for m in messages if morning_start_ts <= m.timestamp <= morning_end_ts]
        if not morning_msgs and mock_mode:
            morning_msgs = messages

        important_items: list[dict[str, Any]] = []
        ordinary_items: list[dict[str, Any]] = []
        bulk_indicators = ("noreply", "no-reply", "newsletter", "unsubscribe", "digest", "promotion", "marketing")
        urgent_terms = _URGENT_KEYWORDS | {"interview", "offer", "invoice", "payment", "security alert", "verification", "meeting", "deadline", "review", "action"}

        for m in sorted(morning_msgs, key=lambda x: x.timestamp, reverse=True):
            subj_l = (m.subject or "").lower()
            snip_l = (m.body_snippet or "").lower()
            sender_l = (m.sender or "").lower()
            labels_u = {str(lb).upper() for lb in (m.labels or [])}

            score = 0
            reasons: list[str] = []
            if m.is_important or "IMPORTANT" in labels_u or "STARRED" in labels_u:
                score += 4
                reasons.append("Gmail IMPORTANT/STARRED label")
            if not m.is_read:
                score += 1
                reasons.append("unread")
            matched_kw = [kw for kw in sorted(urgent_terms) if kw in subj_l or kw in snip_l]
            if matched_kw:
                score += 3
                reasons.append(f"action/urgency signal ({', '.join(matched_kw[:3])})")
            is_bulk = (
                "CATEGORY_PROMOTIONS" in labels_u
                or "CATEGORY_SOCIAL" in labels_u
                or any(b in sender_l or b in snip_l for b in bulk_indicators)
            )
            if is_bulk:
                score -= 3
                reasons.append("automated/promotional sender")

            recv_str = datetime.datetime.fromtimestamp(m.timestamp, tz=now_dt.tzinfo).strftime("%Y-%m-%d %H:%M %Z")
            _strip_u = lambda s: re.sub(r"<<<(?:END[- ]?)?UNTRUSTED(?:-ESCAPED)?(?:\s+source=[^>]*)?>>>", "", s).strip()
            clean_sender = _strip_u(str(m.sender_name or m.sender or "Unknown"))
            clean_subject = _strip_u(str(m.subject or "(No Subject)"))
            clean_snippet = _strip_u(str(m.body_snippet or ""))[:240]
            entry = {
                "message_id": m.message_id,
                "sender": untrusted("gmail", clean_sender),
                "subject": untrusted("gmail", clean_subject),
                "received_time": recv_str,
                "is_read": m.is_read,
                "is_important": score >= 3,
                "importance_score": score,
                "importance_rationale": "; ".join(reasons) if reasons else "standard inbox message",
                "snippet": untrusted("gmail", clean_snippet),
                "_plain_sender": clean_sender,
                "_plain_subject": clean_subject,
                "_plain_snippet": clean_snippet,
            }
            if score >= 3:
                important_items.append(entry)
            else:
                ordinary_items.append(entry)

        important_items = important_items[:limit]
        ordinary_items = ordinary_items[:limit]

        lines = [
            f"Morning Gmail Check ({morning_start_dt.strftime('%Y-%m-%d %H:%M')} - {morning_end_dt.strftime('%H:%M %Z')}) [Source: {source}]:",
            f"Reviewed {len(morning_msgs)} message(s): {len(important_items)} important, {len(ordinary_items)} ordinary.",
        ]
        if important_items:
            lines.append("\nImportant Emails (untrusted external content):")
            for idx, it in enumerate(important_items, 1):
                lines.append(
                    f"{idx}. [{it['received_time']}] From: {it['_plain_sender']} | Subject: {it['_plain_subject']}\n"
                    f"   Why important: {it['importance_rationale']}\n"
                    f"   Summary/Snippet: {it['_plain_snippet']}"
                )
        else:
            lines.append("No high-priority/important emails found in this morning window.")
        if ordinary_items:
            lines.append(f"\nOrdinary Emails ({len(ordinary_items)}): " + ", ".join(str(o["_plain_subject"]) for o in ordinary_items[:3]))

        for it in important_items + ordinary_items:
            it.pop("_plain_sender", None)
            it.pop("_plain_subject", None)
            it.pop("_plain_snippet", None)

        return {
            "connected": True,
            "retrieval_ok": True,
            "source": source,
            "account_email": mask_pii_string(gmail_acc.email_or_id),
            "window_start": morning_start_dt.isoformat(),
            "window_end": morning_end_dt.isoformat(),
            "total_in_window": len(morning_msgs),
            "important_messages": important_items,
            "ordinary_messages": ordinary_items,
            "summary": untrusted("gmail", "\n".join(lines)),
        }
