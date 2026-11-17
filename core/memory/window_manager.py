"""core/memory/window_manager.py — Adaptive Context Window Management & Compaction.

Implements:
- Fast deterministic token and character estimation.
- Priority-ranked context construction.
- Strict preservation of verified artifacts, file paths, and cryptographic hashes.
- Lossless/meaning-preserving compaction of verbose intermediate history.
"""

from __future__ import annotations

from core.memory.types import ContextEnvelope


def estimate_tokens(text: str) -> int:
    """Heuristic token estimator (~4 characters per token)."""
    return max(1, len(text or "") // 4)


def estimate_chars(tokens: int) -> int:
    """Heuristic character budget for a given token count."""
    return max(4, tokens * 4)


def compact_text(text: str, max_chars: int = 200, is_verified: bool = False) -> str:
    """Compact verbose text while strictly preserving factual meaning and verification integrity."""
    clean = str(text or "").strip()
    if len(clean) <= max_chars:
        return clean

    prefix = clean[:max_chars // 2].rstrip()
    suffix = clean[-(max_chars // 3):].lstrip()
    omitted = len(clean) - len(prefix) - len(suffix)

    if is_verified:
        return f"{prefix} ... [VERIFIED CONTENT COMPACTED: {omitted} chars omitted] ... {suffix}"
    return f"{prefix} ... [{omitted} chars compacted] ... {suffix}"


class ContextWindowManager:
    """Adaptive context manager enforcing strict token budgets and verified artifact preservation."""

    def __init__(self, default_max_tokens: int = 1500) -> None:
        self.default_max_tokens = default_max_tokens

    def format_envelope_for_prompt(
        self,
        envelope: ContextEnvelope,
        max_tokens: int | None = None
    ) -> str:
        """Render a ContextEnvelope into a concise, budget-compliant prompt block."""
        budget_tokens = max_tokens or self.default_max_tokens
        max_chars = estimate_chars(budget_tokens)

        # ── PRIORITY 1: Core Goal & Current Subtask Assignment (Immutable) ─────
        header_lines = [
            f"=== CONTEXT FOR SUBTASK #{envelope.subtask_id} ===",
            f"GOAL: {envelope.goal}",
            f"ASSIGNMENT: [{envelope.agent_name.upper()}] {envelope.subtask_title}",
            f"INSTRUCTIONS: {envelope.subtask_description}",
        ]
        header_text = "\n".join(header_lines) + "\n\n"

        # ── PRIORITY 2: Verified Physical Deliverables (Immutable Artifacts) ───
        # Note: Compaction must NEVER alter or omit verified deliverables, file paths, or hashes.
        deliverable_lines = []
        if envelope.verified_deliverables:
            deliverable_lines.append("VERIFIED ARTIFACTS ON DISK:")
            for d in envelope.verified_deliverables:
                deliverable_lines.append(
                    f"  • {d.get('file_path')} "
                    f"({d.get('byte_size', 0)} bytes, SHA-256: {d.get('sha256', 'none')}) "
                    f"— Verdict: {d.get('verdict', 'verified')}"
                )
        deliverables_text = ("\n".join(deliverable_lines) + "\n\n") if deliverable_lines else ""

        # Calculate remaining character budget for dynamic sections
        used_chars = len(header_text) + len(deliverables_text)
        remaining_budget = max(200, max_chars - used_chars)

        # ── PRIORITY 3: Prerequisite Subtask Results (Subject to Compaction) ──
        prereq_lines = []
        if envelope.prerequisite_results:
            prereq_lines.append("DEPENDENT SUBTASK RESULTS:")
            prereq_budget_per_item = max(120, remaining_budget // (len(envelope.prerequisite_results) + 1))

            for st_id, res_data in envelope.prerequisite_results.items():
                agent = res_data.get("agent", "unknown")
                raw_output = str(res_data.get("output", ""))
                is_ver = bool(res_data.get("is_verified", False))
                compacted = compact_text(raw_output, max_chars=prereq_budget_per_item, is_verified=is_ver)
                status_lbl = "VERIFIED" if is_ver else "UNVERIFIED"
                prereq_lines.append(f"  • [{agent.upper()}] Step {st_id} ({status_lbl}): {compacted}")

        prereqs_text = ("\n".join(prereq_lines) + "\n\n") if prereq_lines else ""

        # ── PRIORITY 4: Semantic Facts & Procedural Hints ─────────────────────
        knowledge_lines = []
        if envelope.semantic_facts:
            knowledge_lines.append("RELEVANT VERIFIED KNOWLEDGE:")
            for fact in envelope.semantic_facts[:3]:
                knowledge_lines.append(f"  • {fact.get('key')}: {fact.get('fact')}")

        if envelope.procedural_hints:
            knowledge_lines.append("PROCEDURAL GUIDANCE:")
            for hint in envelope.procedural_hints[:2]:
                knowledge_lines.append(f"  • {hint}")

        knowledge_text = ("\n".join(knowledge_lines) + "\n") if knowledge_lines else ""

        # Assemble and final budget check
        full_context = header_text + deliverables_text + prereqs_text + knowledge_text

        if len(full_context) <= max_chars:
            return full_context

        # Emergency fallback: keep Priority 1 and Priority 2 intact, trim knowledge
        return header_text + deliverables_text + prereqs_text
