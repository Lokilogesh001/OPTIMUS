"""core/missions/storage.py — Atomic persistence and recovery store for OPTIMUS Missions.

Persists MissionRecord objects to disk at `memory/missions/<mission_id>.json` using
atomic file writes (write to temp file, then atomic rename) to guarantee consistency
across unexpected application terminations.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from core.missions.types import MissionRecord, MissionState

logger = logging.getLogger(__name__)


class MissionStore:
    """Thread-safe, atomic disk persistence store for autonomous missions."""

    def __init__(self, storage_dir: Path | str | None = None) -> None:
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = Path.cwd() / "memory" / "missions"
        
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def save(self, record: MissionRecord) -> None:
        """Atomically persist a MissionRecord to disk."""
        with self._lock:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            target_path = self.storage_dir / f"{record.mission_id}.json"
            temp_path = self.storage_dir / f"{record.mission_id}.tmp"

            payload = record.to_dict()
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                
                # Atomic replacement
                if target_path.exists():
                    target_path.unlink()
                temp_path.rename(target_path)
            except Exception as e:
                logger.error("Failed to save mission %s: %s", record.mission_id, e)
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass
                raise

    def get(self, mission_id: str) -> MissionRecord | None:
        """Load a MissionRecord by its ID."""
        with self._lock:
            target_path = self.storage_dir / f"{mission_id}.json"
            if not target_path.exists():
                return None
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return MissionRecord.from_dict(data)
            except Exception as e:
                logger.error("Corrupted mission record at %s: %s", target_path, e)
                return None

    def list_all(self, limit: int = 100) -> list[MissionRecord]:
        """List all stored missions, ordered most recently updated first."""
        with self._lock:
            records: list[MissionRecord] = []
            for file_path in self.storage_dir.glob("*.json"):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    records.append(MissionRecord.from_dict(data))
                except Exception as e:
                    logger.warning("Skipping unreadable mission file %s: %s", file_path, e)
            
            records.sort(key=lambda r: r.updated_at, reverse=True)
            return records[:limit]

    def find_resumable(self) -> list[MissionRecord]:
        """Find missions that were interrupted or are waiting to be resumed.
        
        Resumable states:
        - PAUSED
        - WAITING_FOR_APPROVAL
        - WAITING_FOR_AGENT
        - RETRYING
        - RECOVERING
        - EXECUTE (interrupted mid-execution before clean completion)
        """
        with self._lock:
            all_missions = self.list_all()
            resumable_states = {
                MissionState.PAUSED,
                MissionState.WAITING_FOR_APPROVAL,
                MissionState.WAITING_FOR_AGENT,
                MissionState.RETRYING,
                MissionState.RECOVERING,
                MissionState.EXECUTE,
                MissionState.PLAN,
                MissionState.UNDERSTAND,
            }
            return [m for m in all_missions if m.state in resumable_states]

    def delete(self, mission_id: str) -> bool:
        """Delete a mission record from storage."""
        with self._lock:
            target_path = self.storage_dir / f"{mission_id}.json"
            if target_path.exists():
                try:
                    target_path.unlink()
                    return True
                except Exception as e:
                    logger.error("Failed to delete mission %s: %s", mission_id, e)
                    return False
            return False
