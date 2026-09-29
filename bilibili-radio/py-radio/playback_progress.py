"""Transactional resume checkpoints, independent of listening/counting thresholds."""
from __future__ import annotations

import time
from typing import Any

from database import get_connection
from error_code import APIError
from library_service import utc_now
from models import Track, make_track_id


def nonnegative_int(value: Any, name: str) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        raise APIError.validation_error(f"{name} must be an integer")
    if number < 0 or number > 9_007_199_254_740_991:
        raise APIError.validation_error(f"{name} is out of range")
    return number


def record_progress(service: Any, payload: dict[str, Any]) -> dict[str, Any]:
    session_id = str(payload.get("sessionId") or payload.get("session_id") or "").strip()
    track_id = str(payload.get("trackId") or payload.get("track_id") or "").strip()
    event = str(payload.get("event") or "heartbeat").lower()
    if not session_id or len(session_id) > 160 or not track_id:
        raise APIError.validation_error("Valid sessionId and trackId are required")
    if event not in {"start", "play", "heartbeat", "pause", "seek", "end", "ended", "skip", "next", "change", "stop", "quit"}:
        raise APIError.validation_error("Unsupported playback event")
    track = service.library.get_track(track_id)
    if not track and isinstance(payload.get("track"), dict):
        try:
            track = Track.from_dict(payload["track"])
        except (TypeError, ValueError):
            raise APIError.validation_error("Invalid track")
        if track_id != make_track_id(track.bvid, track.cid):
            raise APIError.validation_error("trackId does not match bvid/cid")
        track.track_id = track_id
        service.library.upsert_track(track)
    if not track:
        raise APIError.not_found(f"Track not found: {track_id}")
    position = nonnegative_int(payload.get("positionMs", payload.get("position_ms")), "positionMs")
    if track.duration > 0:
        position = min(position, track.duration * 1000)
    listened = nonnegative_int(payload.get("listenMs", payload.get("listen_ms")), "listenMs")
    supplied_seq = payload.get("eventSeq")
    seq = nonnegative_int(supplied_seq, "eventSeq") if supplied_seq is not None else None
    started = nonnegative_int(payload.get("sessionStartedAtMs"), "sessionStartedAtMs") or int(time.time() * 1000)
    if started > int(time.time() * 1000) + 300_000:
        raise APIError.validation_error("sessionStartedAtMs is in the future")
    completed = bool(payload.get("completed")) or service._is_completed(position, track.duration)
    # Statistical completion (90%) must not discard the unplayed ending on resume.
    ended = event in {"end", "ended"}
    now = utc_now()
    with get_connection(service.db_path) as conn:
        # Serialize read/modify/write across request threads, including duplicates.
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute("SELECT * FROM playback_sessions WHERE user_id = ? AND session_id = ?",
                             (service.user_id, session_id)).fetchone()
        if prior and prior["track_id"] != track_id:
            raise APIError.validation_error("A session cannot change tracks")
        if prior and seq is not None and seq <= prior["event_seq"]:
            return {"accepted": False, "sessionId": session_id, "trackId": track_id, "event": event}
        seq = seq if seq is not None else (prior["event_seq"] + 1 if prior else 1)
        started = prior["session_started_ms"] if prior else started
        listened = max(listened, prior["listen_ms"] if prior else 0)
        counted = bool(prior and prior["recent_counted"])
        qualifies = listened >= service._recent_threshold_ms(track.duration) or completed
        increment = int(qualifies and not counted)
        skipped = event in {"skip", "next", "change", "stop"} and listened < 15_000 and not completed
        conn.execute("""
            INSERT INTO playback_sessions (user_id, session_id, track_id, started_at,
                ended_at, last_position_ms, listen_ms, completed, skipped, last_event,
                event_seq, session_started_ms, recent_counted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                ended_at = excluded.ended_at, last_position_ms = excluded.last_position_ms,
                listen_ms = excluded.listen_ms, completed = excluded.completed,
                skipped = excluded.skipped, last_event = excluded.last_event,
                event_seq = excluded.event_seq, recent_counted = excluded.recent_counted
        """, (service.user_id, session_id, track_id, now,
              now if event in {"pause", "end", "ended", "change", "stop", "quit"} else None,
              position, listened, int(completed), int(skipped), event, seq, started, int(counted or qualifies)))
        progress = conn.execute("""
            INSERT INTO playback_progress (user_id, track_id, session_id, session_started_ms,
                event_seq, position_ms, listen_ms, completed, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, track_id) DO UPDATE SET
                session_id = excluded.session_id, session_started_ms = excluded.session_started_ms,
                event_seq = excluded.event_seq, position_ms = excluded.position_ms,
                listen_ms = excluded.listen_ms, completed = excluded.completed, updated_at = excluded.updated_at
            WHERE (excluded.session_started_ms, excluded.session_id) >
                    (playback_progress.session_started_ms, playback_progress.session_id)
               OR (excluded.session_id = playback_progress.session_id
                    AND excluded.event_seq > playback_progress.event_seq)
        """, (service.user_id, track_id, session_id, started, seq, position, listened, int(ended), now))
        if event != "heartbeat":
            conn.execute("""INSERT INTO playback_events
                (user_id, session_id, track_id, event, position_ms, listen_ms, completed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                         (service.user_id, session_id, track_id, event, position, listened, int(completed), now))
        if qualifies and progress.rowcount:
            conn.execute("""
                INSERT INTO recent (user_id, track_id, last_played_at, play_count, position_ms, listen_ms, completed)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, track_id) DO UPDATE SET
                    last_played_at = excluded.last_played_at,
                    play_count = recent.play_count + ?, position_ms = excluded.position_ms,
                    listen_ms = MAX(recent.listen_ms, excluded.listen_ms), completed = excluded.completed
            """, (service.user_id, track_id, now, max(1, increment), position, listened, int(completed), increment))
            conn.execute("""
                INSERT INTO playback_recent (user_id, track_id, last_played_at, position_ms, listen_ms, completed, skipped)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, track_id) DO UPDATE SET last_played_at = excluded.last_played_at,
                    position_ms = excluded.position_ms, listen_ms = excluded.listen_ms,
                    completed = excluded.completed, skipped = excluded.skipped
            """, (service.user_id, track_id, now, position, listened, int(completed), int(skipped)))
    return {"accepted": True, "sessionId": session_id, "trackId": track_id, "eventSeq": seq,
            "positionMs": position, "listenMs": listened, "completed": completed,
            "skipped": skipped, "event": event, "recentCounted": bool(increment)}


def read_progress(service: Any, track_id: str) -> dict[str, Any]:
    with get_connection(service.db_path) as conn:
        row = conn.execute("""SELECT position_ms, listen_ms, completed, updated_at,
                session_started_ms, session_id, event_seq FROM playback_progress
                WHERE user_id = ? AND track_id = ?""", (service.user_id, track_id)).fetchone()
        if row:
            return {"trackId": track_id, "positionMs": row["position_ms"], "listenMs": row["listen_ms"],
                    "completed": bool(row["completed"]), "lastPlayedAt": row["updated_at"],
                    "sessionStartedAtMs": row["session_started_ms"], "sessionId": row["session_id"],
                    "eventSeq": row["event_seq"]}
        # Preserve old installations' history without creating fake new sessions.
        row = conn.execute("""
            SELECT position_ms, listen_ms, last_played_at FROM (
                SELECT position_ms, listen_ms, last_played_at FROM recent WHERE user_id = ? AND track_id = ?
                UNION ALL
                SELECT position_ms, listen_ms, last_played_at FROM playback_recent WHERE user_id = ? AND track_id = ?
                UNION ALL
                SELECT last_position_ms, listen_ms, COALESCE(ended_at, started_at)
                    FROM playback_sessions WHERE user_id = ? AND track_id = ?
            ) ORDER BY last_played_at DESC LIMIT 1
        """, (service.user_id, track_id) * 3).fetchone()
    track = service.library.get_track(track_id)
    position = row["position_ms"] if row else 0
    ended = bool(track and track.duration and position >= track.duration * 1000 - 1000)
    return {"trackId": track_id, "positionMs": position, "listenMs": row["listen_ms"] if row else 0,
            "completed": ended, "lastPlayedAt": row["last_played_at"] if row else None}
