"""core/computer_use/understanding.py — Visual and semantic UI understanding pipeline.

Implements Phase 2 of Milestone 9: Computer Use & Visual Intelligence.
- Control matching by name, role, and text with calibrated confidence scoring.
- Disambiguation across similar controls.
- Detection of dialogs, modal popups, error notices, loading states, and confirmation gates.
- Observation diffing to verify state transitions without assuming success.
- Strict refusal to hallucinate coordinates when confidence is low or ambiguous.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from core.computer_use.types import DesktopObservation, UIElement


class VisualUnderstandingPipeline:
    """Interprets UI elements and observations to determine semantic state and actionable targets."""

    def __init__(self, confidence_threshold: float = 0.60) -> None:
        self.confidence_threshold = confidence_threshold

    def calculate_match_confidence(
        self, element: UIElement, query: str | None, role: str | None
    ) -> float:
        """Calculate a calibrated confidence score between 0.0 and 1.0 for an element match."""
        if not query and not role:
            return 0.0

        score = 0.0
        weights_total = 0.0

        # Role matching
        if role:
            weights_total += 0.3
            if element.role.lower() == role.lower():
                score += 0.3
            elif role.lower() in element.role.lower():
                score += 0.2

        # Query matching (against name, value, or metadata)
        if query:
            weights_total += 0.7
            q_clean = query.strip().lower()
            name_clean = element.name.strip().lower()
            val_clean = element.value.strip().lower()

            if q_clean == name_clean:
                score += 0.7
            elif q_clean in name_clean:
                # Substring bonus inversely scaled by length difference
                ratio = len(q_clean) / max(len(name_clean), 1)
                score += 0.5 + (0.2 * ratio)
            elif name_clean and q_clean in name_clean.split():
                score += 0.6
            elif q_clean == val_clean:
                score += 0.55
            elif q_clean in val_clean:
                score += 0.45
            else:
                # Fuzzy sequence matcher
                similarity = difflib.SequenceMatcher(None, q_clean, name_clean).ratio()
                if similarity >= 0.75:
                    score += 0.7 * similarity
                elif similarity >= 0.5:
                    score += 0.4 * similarity

        normalized_score = score / weights_total if weights_total > 0 else 0.0
        return min(max(normalized_score, 0.0), 1.0)

    def find_elements(
        self,
        obs: DesktopObservation,
        query: str | None = None,
        role: str | None = None,
        min_confidence: float | None = None,
    ) -> list[tuple[UIElement, float]]:
        """Find matching elements sorted by confidence descending."""
        threshold = min_confidence if min_confidence is not None else self.confidence_threshold
        results: list[tuple[UIElement, float]] = []

        for el in obs.elements:
            conf = self.calculate_match_confidence(el, query, role)
            if conf >= threshold:
                results.append((el, conf))

        results.sort(key=lambda item: item[1], reverse=True)
        return results

    def find_best_element(
        self, obs: DesktopObservation, query: str, role: str | None = None
    ) -> tuple[UIElement | None, float, str]:
        """Find the single best element or flag ambiguity/uncertainty.
        
        Returns:
            (UIElement | None, confidence_score, status_explanation)
        """
        matches = self.find_elements(obs, query=query, role=role, min_confidence=0.4)
        if not matches:
            return None, 0.0, f"No element found matching '{query}' (role={role or 'any'})"

        best_el, best_conf = matches[0]

        if best_conf < self.confidence_threshold:
            return None, best_conf, (
                f"Best match for '{query}' ('{best_el.name}', role={best_el.role}) "
                f"confidence {best_conf:.2f} is below safety threshold {self.confidence_threshold:.2f}."
            )

        # Check for ambiguity among top candidates
        if len(matches) > 1:
            second_el, second_conf = matches[1]
            if (best_conf - second_conf) < 0.08 and best_el.name != second_el.name:
                return None, best_conf, (
                    f"Ambiguous match for '{query}': conflicting candidates "
                    f"'{best_el.name}' (role={best_el.role}, conf={best_conf:.2f}) and "
                    f"'{second_el.name}' (role={second_el.role}, conf={second_conf:.2f}). "
                    f"Disambiguate with specific role or exact label."
                )

        return best_el, best_conf, "Exact or confident element located"

    def classify_ui_state(self, obs: DesktopObservation) -> dict[str, Any]:
        """Analyze the current observation to detect dialogs, loading states, and interactive roles."""
        buttons: list[str] = []
        inputs: list[str] = []
        dialogs: list[str] = []
        is_loading = False
        is_error = False
        error_messages: list[str] = []

        loading_keywords = ("loading", "working on it", "please wait", "processing", "syncing")
        error_keywords = ("error", "failed", "access denied", "exception", "critical", "warning", "unable to")

        for el in obs.elements:
            name_lower = el.name.lower()
            val_lower = el.value.lower()
            role_lower = el.role.lower()

            if role_lower in ("button", "splitbutton"):
                if el.name:
                    buttons.append(el.name)
            elif role_lower in ("edit", "combobox", "textbox"):
                inputs.append(el.name or el.value or f"input_{el.element_id}")
            elif role_lower in ("dialog", "alert"):
                dialogs.append(el.name or "Unnamed Dialog")

            # Loading indicator detection
            if role_lower in ("progressbar", "spinner") or any(k in name_lower or k in val_lower for k in loading_keywords):
                is_loading = True

            # Error state detection
            if any(k in name_lower or k in val_lower for k in error_keywords):
                if role_lower in ("text", "alert", "dialog", "status"):
                    is_error = True
                    error_messages.append(el.name or el.value)

        # Primary action detection
        primary_candidates = ("ok", "yes", "save", "submit", "confirm", "next", "continue", "finish", "apply")
        primary_action = None
        for cand in primary_candidates:
            matched_btn = next((b for b in buttons if b.strip().lower() == cand), None)
            if matched_btn:
                primary_action = matched_btn
                break

        is_confirmation = bool(
            dialogs and any(b.lower() in ("yes", "ok", "confirm") for b in buttons) and any(b.lower() in ("no", "cancel") for b in buttons)
        )

        return {
            "app": obs.active_app,
            "title": obs.window_title,
            "element_count": len(obs.elements),
            "is_dialog": bool(dialogs) or "dialog" in obs.window_title.lower(),
            "dialogs": dialogs,
            "is_loading": is_loading,
            "is_error": is_error,
            "error_messages": error_messages,
            "is_confirmation": is_confirmation,
            "primary_action": primary_action,
            "buttons": buttons,
            "inputs": inputs,
        }

    def diff_observations(
        self, before: DesktopObservation, after: DesktopObservation
    ) -> dict[str, Any]:
        """Compute structured semantic differences between two sequential observations."""
        window_changed = (before.active_app != after.active_app) or (before.window_title != after.window_title)
        digest_changed = before.digest != after.digest

        before_map = {e.element_id: e for e in before.elements}
        after_map = {e.element_id: e for e in after.elements}

        added_ids = set(after_map.keys()) - set(before_map.keys())
        removed_ids = set(before_map.keys()) - set(after_map.keys())

        added_elements = [
            {"id": eid, "role": after_map[eid].role, "name": after_map[eid].name}
            for eid in added_ids
        ]
        removed_elements = [
            {"id": eid, "role": before_map[eid].role, "name": before_map[eid].name}
            for eid in removed_ids
        ]

        text_updates: list[dict[str, Any]] = []
        for eid, b_el in before_map.items():
            if eid in after_map:
                a_el = after_map[eid]
                if b_el.value != a_el.value:
                    text_updates.append({
                        "element_id": eid,
                        "name": a_el.name,
                        "old_value": b_el.value,
                        "new_value": a_el.value,
                    })

        state_changed = window_changed or digest_changed or bool(added_elements) or bool(removed_elements) or bool(text_updates)

        return {
            "state_changed": state_changed,
            "window_changed": window_changed,
            "digest_changed": digest_changed,
            "before_digest": before.digest,
            "after_digest": after.digest,
            "before_app": before.active_app,
            "after_app": after.active_app,
            "before_title": before.window_title,
            "after_title": after.window_title,
            "added_count": len(added_elements),
            "removed_count": len(removed_elements),
            "text_updates_count": len(text_updates),
            "added_elements": added_elements[:20],
            "removed_elements": removed_elements[:20],
            "text_updates": text_updates[:20],
        }


_GLOBAL_UNDERSTANDING: VisualUnderstandingPipeline | None = None


def get_visual_understanding() -> VisualUnderstandingPipeline:
    """Retrieve or initialize the global visual understanding pipeline."""
    global _GLOBAL_UNDERSTANDING
    if _GLOBAL_UNDERSTANDING is None:
        _GLOBAL_UNDERSTANDING = VisualUnderstandingPipeline()
    return _GLOBAL_UNDERSTANDING
