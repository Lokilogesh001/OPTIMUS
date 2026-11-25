"""actions/media_control.py — Live Voice Tool for Strict Spotify Media Control.

Routes Spotify playback, search, pause, resume, next, previous, and verification
strictly through OPTIMUS's deterministic MediaAdapter (Spotify CLI + Desktop UI
+ audio playback verification), strictly forbidding unsolicited YouTube fallbacks.
"""

from __future__ import annotations

from typing import Any

from core import outcome
from core.application_intelligence.adapters.media import MediaAdapter
from core.application_intelligence.types import ActionContract


def media_control(
    parameters: dict[str, Any] | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Control Spotify playback deterministically with zero YouTube fallback."""
    params = dict(parameters or {})
    action = str(params.get("action") or "play").strip().lower()
    query = str(params.get("query") or params.get("track") or params.get("song") or "").strip()
    mode = str(params.get("mode") or "desktop").strip().lower()

    op_map = {
        "play": "play_spotify_strict",
        "search_and_play": "play_spotify_strict",
        "next": "next_track",
        "previous": "previous_track",
        "pause": "pause",
        "resume": "resume",
        "verify": "verify_playback",
        "status": "verify_playback",
    }
    op = op_map.get(action, action)
    adapter = MediaAdapter()
    ev = adapter.execute(ActionContract(
        action_id=f"voice_media_{action}",
        target_app="spotify web" if mode == "web" else "spotify",
        operation=op,
        parameters={"query": query, "mode": mode, "strict_spotify": True},
    ))

    details = " ".join(ev.facts or []) or (ev.error_message or "")
    if ev.status == "done":
        return outcome.done("media_control", app="spotify", action=action, query=query, details=details, **(ev.evidence_data or {}))
    if ev.status == "unverified":
        return outcome.unverified("media_control", reason=ev.error_message or details, app="spotify", action=action, query=query)
    return outcome.failed("media_control", ev.error_message or details or "Spotify operation failed", app="spotify", action=action)


TOOL = {
    "name": "media_control",
    "description": (
        "Strict Spotify media controller (play, search_and_play, pause, resume, next, previous, status/verify). "
        "ALWAYS use this tool when the user asks to open Spotify or play a song on Spotify. "
        "Do NOT fall back to youtube_video or web browser search when Spotify cannot find or play a track."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "play | search_and_play | pause | resume | next | previous | status | verify",
            },
            "query": {
                "type": "STRING",
                "description": "Song / artist / movie query to play on Spotify",
            },
            "mode": {
                "type": "STRING",
                "description": "desktop (default) | web",
            },
        },
        "required": ["action"],
    },
    "handler": media_control,
}
