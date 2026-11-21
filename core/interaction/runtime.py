"""core/interaction/runtime.py — Unified Interaction Runtime for OPTIMUS.

The central coordinator connecting:
- Multi-modal user input (text, voice, image attachments).
- Intent classification and strict capability/privacy boundaries.
- Speech intelligence and non-destructive barge-in / cancellation.
- Sovereign Central Orchestrator execution handoff.
- Multi-turn conversation sessions and memory bus promotion.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

logger = logging.getLogger("optimus.interaction.runtime")

from core.intelligence.gateway import IntelligenceGateway
from core.intelligence.types import ChatMessage, ModelRequest
from core.interaction.events import (
    AgentResponseCompletedEvent,
    IntentClassifiedEvent,
    TaskCancellationRequestedEvent,
    UserMessageReceivedEvent,
)
from core.interaction.router import InteractionRouter
from core.interaction.session import InteractionSessionManager, redact_secrets
from core.interaction.types import (
    ImageAttachment,
    IntentCategory,
    InteractionModality,
    InteractionRole,
)
from core.interaction.voice_bridge import VoiceIntelligenceBridge
from core.memory import OptimusMemoryManager, get_memory_manager
from core.orchestrator.desktop import ensure as ensure_orchestrator
from core.orchestrator.events import (
    EventBus,
    TaskCancelledEvent,
    TaskCompletedEvent,
    TaskFailedEvent,
)
from core.orchestrator.orchestrator import CentralOrchestrator


class InteractionRuntime:
    """Coordinates conversational sessions, speech intelligence, and sovereign orchestrator execution."""

    _instance: InteractionRuntime | None = None
    _singleton_lock = threading.Lock()

    def __init__(
        self,
        orchestrator: CentralOrchestrator | None = None,
        gateway: IntelligenceGateway | None = None,
        memory_manager: OptimusMemoryManager | None = None,
        event_bus: EventBus | None = None,
        session_manager: InteractionSessionManager | None = None,
        voice_bridge: VoiceIntelligenceBridge | None = None,
        router: InteractionRouter | None = None,
    ) -> None:
        self.orchestrator = orchestrator or ensure_orchestrator()
        self.event_bus = event_bus or (self.orchestrator.event_bus if self.orchestrator else EventBus())
        self.gateway = gateway or IntelligenceGateway.instance()
        self.memory_manager = memory_manager or getattr(self.orchestrator, "memory_manager", None) or get_memory_manager()
        self.session_manager = session_manager or InteractionSessionManager()
        self.voice_bridge = voice_bridge or VoiceIntelligenceBridge(event_bus=self.event_bus)
        self.router = router or InteractionRouter(gateway=self.gateway)

        self._lock = threading.RLock()
        self._subscribe_orchestrator_events()

    @classmethod
    def instance(cls) -> InteractionRuntime:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._singleton_lock:
            cls._instance = None

    def _subscribe_orchestrator_events(self) -> None:
        """Listen for orchestrator lifecycle events to update interaction sessions."""
        if not self.event_bus:
            return

        def _on_task_done(e: TaskCompletedEvent) -> None:
            # Promote deliverable to memory
            delivs = getattr(e, "deliverables", ())
            task_id = getattr(e, "task_id", "")
            res = getattr(e, "result", "")
            if self.memory_manager and delivs:
                for deliv in delivs:
                    if hasattr(self.memory_manager, "store_deliverable"):
                        self.memory_manager.store_deliverable(
                            task_id=task_id,
                            deliverable_path=str(deliv),
                            verified=True,
                        )
            # Notify any session that owns this task
            for s_id, s_state in self.session_manager._sessions.items():
                if s_state.active_task_id == task_id:
                    self.session_manager.set_active_task(s_id, None)
                    msg_text = (
                        f"Directive completed successfully [Task {task_id[:8]}]. "
                        f"Outcome: {res[:180] if res else 'Verified physical deliverables recorded.'}"
                    )
                    self.session_manager.add_message(
                        session_id=s_id,
                        role=InteractionRole.ASSISTANT,
                        content=msg_text,
                        metadata={"task_id": task_id, "status": "COMPLETED"},
                    )

        def _on_task_failed(e: TaskFailedEvent) -> None:
            for s_id, s_state in self.session_manager._sessions.items():
                if s_state.active_task_id == e.task_id:
                    self.session_manager.set_active_task(s_id, None)
                    msg_text = f"Directive execution failed [Task {e.task_id[:8]}]: {e.error[:150]}"
                    self.session_manager.add_message(
                        session_id=s_id,
                        role=InteractionRole.ASSISTANT,
                        content=msg_text,
                        metadata={"task_id": e.task_id, "status": "FAILED"},
                    )

        def _on_task_cancelled(e: TaskCancelledEvent) -> None:
            for s_id, s_state in self.session_manager._sessions.items():
                if s_state.active_task_id == e.task_id:
                    self.session_manager.set_active_task(s_id, None)
                    msg_text = f"Directive cancelled [Task {e.task_id[:8]}]: {e.reason}"
                    self.session_manager.add_message(
                        session_id=s_id,
                        role=InteractionRole.ASSISTANT,
                        content=msg_text,
                        metadata={"task_id": e.task_id, "status": "CANCELLED"},
                    )

        self.event_bus.subscribe(TaskCompletedEvent, _on_task_done)
        self.event_bus.subscribe(TaskFailedEvent, _on_task_failed)
        self.event_bus.subscribe(TaskCancelledEvent, _on_task_cancelled)

    async def process_turn(
        self,
        session_id: str,
        text: str,
        modality: InteractionModality = InteractionModality.TEXT,
        attachments: list[ImageAttachment] | None = None,
        speak_response: bool = False,
    ) -> str:
        """Process an incoming turn across any modality and return the assistant response."""
        clean_text = text.strip()
        att_list = list(attachments or [])

        # 1. Deduplication Check for Voice Inputs
        if modality == InteractionModality.VOICE and self.voice_bridge.is_duplicate(clean_text):
            print(f"[InteractionRuntime] Suppressed duplicate speech utterance: '{clean_text}'")
            return ""

        # 2. Add User Message to Session History
        user_msg = self.session_manager.add_message(
            session_id=session_id,
            role=InteractionRole.USER,
            content=clean_text,
            modality=modality,
            attachments=att_list,
        )

        self.event_bus.publish(
            UserMessageReceivedEvent(
                session_id=session_id,
                message_id=user_msg.message_id,
                modality=modality,
                text=clean_text,
                attachments=tuple(att_list),
            )
        )

        # 3. Classify Intent
        classification = self.router.classify_intent(clean_text, att_list)
        self.event_bus.publish(
            IntentClassifiedEvent(
                session_id=session_id,
                intent=classification.intent,
                confidence=classification.confidence,
                requires_orchestrator=classification.requires_orchestrator,
                target_goal=classification.target_goal,
                explanation=classification.explanation,
            )
        )

        response_text = ""

        # 4. Route Execution Based on Intent

        # Branch A: Voice Output Interruption ("stop speaking")
        if classification.intent == IntentCategory.STOP_SPEAKING:
            self.voice_bridge.stop_speaking(reason="stop_speaking")
            response_text = "Speech playback stopped."
            self._finalize_turn(session_id, response_text, speak_response=False)
            return response_text

        # Branch B: Explicit Task Cancellation ("cancel task")
        if classification.intent == IntentCategory.CANCEL_TASK:
            self.voice_bridge.stop_speaking(reason="task_cancel")
            session = self.session_manager.get_or_create_session(session_id)
            active_tid = session.active_task_id
            if active_tid and self.orchestrator:
                self.orchestrator.cancel_task(active_tid)
                self.session_manager.set_active_task(session_id, None)
                self.event_bus.publish(
                    TaskCancellationRequestedEvent(
                        session_id=session_id,
                        task_id=active_tid,
                        reason="user_cancel",
                    )
                )
                response_text = f"Directive cancellation requested for Task [{active_tid[:8]}]."
            else:
                response_text = "No active background task is currently running to cancel."

            self._finalize_turn(session_id, response_text, speak_response=speak_response)
            return response_text

        # Branch C: System Telemetry Query
        if classification.intent == IntentCategory.SYSTEM_QUERY:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            agent_count = len(self.orchestrator.agent_registry.list_agents()) if self.orchestrator else 0
            response_text = (
                f"OPTIMUS System Status: CPU {cpu:.1f}%, RAM {mem:.1f}%. "
                f"Registered specialized agents: {agent_count}. System operational."
            )
            self._finalize_turn(session_id, response_text, speak_response=speak_response)
            return response_text

        # Branch D: Actionable Task Directive (delegated to Central Orchestrator)
        if classification.intent == IntentCategory.ACTIONABLE_TASK:
            goal = classification.target_goal or clean_text
            if self.orchestrator:
                # Fast-path check for deterministic execution (e.g. Gmail status/retrieval, math, system vitals)
                fast_res = self.orchestrator._try_fast_path(goal, f"fast_{int(time.time()*1000)}")
                if fast_res is not None:
                    _success, summary, _agent, _side_effects = fast_res
                    response_text = summary
                else:
                    record = self.orchestrator.start(goal)
                    self.session_manager.set_active_task(session_id, record.task_id)
                    sub_count = len(record.plan.subtasks) if record.plan else 0
                    response_text = (
                        f"OPTIMUS directive initialized. Task [{record.task_id[:8]}] scheduled with "
                        f"{sub_count} planned subtask(s). Delegated to specialized agents under physical verification."
                    )
            else:
                response_text = f"Central Orchestrator unavailable to execute directive: '{goal}'."

            self._finalize_turn(session_id, response_text, speak_response=speak_response)
            return response_text

        # Branch E: Conversational Turn with Memory Retrieval & Intelligence Gateway
        if classification.requires_vision and not self.router._check_vision_support(
            self.gateway.router.active_policy, att_list
        )[0]:
            # Explain limitation rather than failing or silently changing privacy
            response_text = classification.explanation or "Unable to process image under current privacy policy."
            self._finalize_turn(session_id, response_text, speak_response=speak_response)
            return response_text

        # Retrieve relevant memory facts and connected account statuses
        memory_ctx = ""
        if self.memory_manager:
            try:
                memories = self.memory_manager.search(clean_text, limit=3)
                if memories:
                    memory_ctx = "\n".join(f"- {m.key}: {str(m.content)[:100]}" for m in memories)
            except Exception as e:
                print(f"[InteractionRuntime] Memory retrieval warning: {e}")

        # Inject connected account status into conversational context so assistant knows provider states
        account_ctx = ""
        try:
            from core.personal_intelligence.storage import PersonalStore
            accs = PersonalStore.instance().list_accounts()
            if accs:
                acc_lines = [f"- {a.provider.value}: {a.email_or_id} (Status: {a.auth_status.value})" for a in accs]
                account_ctx = "Connected Accounts:\n" + "\n".join(acc_lines)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed injecting connected accounts: %s", e)

        merged_ctx = f"{memory_ctx}\n\n{account_ctx}".strip() if account_ctx else memory_ctx

        # Assemble prompt history
        history_msgs = self.session_manager.format_prompt_history(session_id, memory_context=merged_ctx)
        chat_messages = [
            ChatMessage(role=m["role"], content=m["content"]) for m in history_msgs
        ]

        # Dispatch generation to Model & Intelligence Gateway
        try:
            req = ModelRequest(
                messages=chat_messages,
                max_tokens=500,
                temperature=0.7,
            )
            resp = self.gateway.complete(req)
            raw_content = resp.text or "No response generated."
            response_text = redact_secrets(raw_content)
        except Exception as e:
            print(f"[InteractionRuntime] Intelligence gateway generation error: {e}")
            response_text = f"OPTIMUS response unavailable due to model error: {e}"

        self._finalize_turn(session_id, response_text, speak_response=speak_response)
        return response_text

    def _finalize_turn(self, session_id: str, response_text: str, speak_response: bool = False) -> None:
        """Save assistant reply, emit completion event, and trigger voice output if requested."""
        clean_reply = redact_secrets(response_text)
        asst_msg = self.session_manager.add_message(
            session_id=session_id,
            role=InteractionRole.ASSISTANT,
            content=clean_reply,
            modality=InteractionModality.TEXT,
        )

        self.event_bus.publish(
            AgentResponseCompletedEvent(
                session_id=session_id,
                message_id=asst_msg.message_id,
                full_text=clean_reply,
                duration_seconds=0.0,
            )
        )

        if speak_response and clean_reply:
            self.voice_bridge.speak(clean_reply)

    def process_turn_sync(
        self,
        session_id: str,
        text: str,
        modality: InteractionModality = InteractionModality.TEXT,
        attachments: list[ImageAttachment] | None = None,
        speak_response: bool = False,
    ) -> str:
        """Synchronous wrapper for process_turn."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    lambda: asyncio.run(
                        self.process_turn(session_id, text, modality, attachments, speak_response)
                    )
                ).result()
        else:
            return asyncio.run(
                self.process_turn(session_id, text, modality, attachments, speak_response)
            )
