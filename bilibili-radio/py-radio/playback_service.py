from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from database import DEFAULT_DB_PATH, LEGACY_OWNER_USER_ID, get_connection, init_db
from library_service import LibraryService
from playback_progress import read_progress, record_progress
from retention import now_ms, PROGRESS_TTL_MS


RECENT_RECORD_RATIO = 0.1
QUICK_SKIP_MS = 15_000
COMPLETE_REMAINING_MS = 30_000


class PlaybackService:
    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        user_id: str = LEGACY_OWNER_USER_ID,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.user_id = user_id
        init_db(self.db_path)
        self.library = LibraryService(self.db_path, user_id=self.user_id)

    def record_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        return record_progress(self, payload)

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT t.*, pr.last_played_at,
                       CASE WHEN CAST((julianday(pr.last_played_at)-2440587.5)*86400000 AS INTEGER) > ?
                       THEN pr.position_ms ELSE 0 END AS position_ms, pr.listen_ms,
                       pr.completed, pr.skipped
                FROM playback_recent pr
                JOIN tracks t ON t.track_id = pr.track_id
                WHERE pr.user_id = ? AND pr.skipped = 0
                ORDER BY pr.last_played_at DESC
                LIMIT ?
                """,
                (now_ms()-PROGRESS_TTL_MS, self.user_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            track = self.library._track_from_row(row).to_dict()
            track.update(
                {
                    "lastPlayedAt": row["last_played_at"],
                    "positionMs": row["position_ms"],
                    "listenMs": row["listen_ms"],
                    "completed": bool(row["completed"]),
                    "skipped": bool(row["skipped"]),
                }
            )
            result.append(track)
        return result

    def get_resume(self, track_id: str) -> dict[str, Any]:
        return read_progress(self, track_id)

    @staticmethod
    def _is_completed(position_ms: int, duration_seconds: int) -> bool:
        if duration_seconds <= 0:
            return False
        duration_ms = duration_seconds * 1000
        return position_ms >= duration_ms * 0.9 or (
            duration_ms > COMPLETE_REMAINING_MS and duration_ms - position_ms <= COMPLETE_REMAINING_MS
        )

    @staticmethod
    def _recent_threshold_ms(duration_seconds: int) -> int:
        if duration_seconds <= 0:
            return QUICK_SKIP_MS
        return max(1_000, int(duration_seconds * 1000 * RECENT_RECORD_RATIO))
