"""Shared retention policy; importing this module never opens a database."""
from datetime import datetime
import time

DAY_MS = 86_400_000
PROGRESS_TTL_MS = DAY_MS
EXPOSURE_TTL_MS = 14 * DAY_MS
METADATA_TTL_MS = 30 * DAY_MS


def now_ms() -> int:
    return int(time.time() * 1000)


def timestamp_ms(value) -> int:
    try:
        return int(datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return 0


def touch_tracks(conn, track_ids, origin: str, *, used: bool = True) -> None:
    """Usage is explicit; generic metadata refresh timestamps are not retention clocks."""
    stamp = now_ms()
    conn.executemany("""UPDATE tracks SET
        first_seen_ms = CASE WHEN first_seen_ms = 0 THEN ? ELSE first_seen_ms END,
        last_used_ms = CASE WHEN ? THEN MAX(last_used_ms, ?) ELSE last_used_ms END,
        ingest_source = CASE WHEN ingest_source = 'unknown' THEN ? ELSE ingest_source END
        WHERE track_id = ?""", [(stamp, int(used), stamp, origin, tid) for tid in track_ids])


def migrate_retention(conn) -> None:
    # Legacy origin is unknowable. Give existing metadata a full grace period.
    conn.execute("UPDATE tracks SET first_seen_ms = ?, ingest_source='legacy' WHERE first_seen_ms = 0", (now_ms(),))
    conn.execute("""CREATE TRIGGER tracks_first_seen AFTER INSERT ON tracks
        WHEN NEW.first_seen_ms = 0 BEGIN
          UPDATE tracks SET first_seen_ms=CAST((julianday('now')-2440587.5)*86400000 AS INTEGER)
          WHERE track_id=NEW.track_id;
        END""")
    conn.execute("""CREATE TABLE recommendation_feedback (
        user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        track_id TEXT NOT NULL REFERENCES tracks(track_id) ON DELETE CASCADE,
        clicked INTEGER NOT NULL DEFAULT 0, played_seconds INTEGER NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0, liked INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
        PRIMARY KEY(user_id, track_id))""")
    conn.execute("""INSERT INTO recommendation_feedback
        SELECT user_id, track_id, MAX(clicked), MAX(played_seconds), MAX(completed),
               MAX(liked), MAX(skipped), MAX(recommended_at)
        FROM recommendation_history
        WHERE clicked <> 0 OR played_seconds > 0 OR completed <> 0 OR liked <> 0 OR skipped <> 0
        GROUP BY user_id, track_id""")
    conn.execute("""INSERT INTO recommendation_feedback
        SELECT user_id, track_id, MAX(event IN ('played','accepted','completed')), 0,
               MAX(event='completed'), MAX(event='liked'),
               MAX(event IN ('skipped','dismissed','dislike')), MAX(created_at)
        FROM recommendation_events WHERE event <> 'shown' GROUP BY user_id, track_id
        ON CONFLICT(user_id,track_id) DO UPDATE SET
          clicked=MAX(recommendation_feedback.clicked,excluded.clicked),
          completed=MAX(recommendation_feedback.completed,excluded.completed),
          liked=MAX(recommendation_feedback.liked,excluded.liked),
          skipped=MAX(recommendation_feedback.skipped,excluded.skipped),
          updated_at=MAX(recommendation_feedback.updated_at,excluded.updated_at)""")
    # Feedback-only rows were previously inserted into the exposure history.
    conn.execute("""DELETE FROM recommendation_history WHERE NOT EXISTS (
        SELECT 1 FROM recommendation_events e WHERE e.user_id=recommendation_history.user_id
        AND e.track_id=recommendation_history.track_id AND e.event='shown'
        AND e.created_at=recommendation_history.recommended_at)""")
    conn.execute("""UPDATE recommendation_history SET
        clicked=0, played_seconds=0, completed=0, liked=0, skipped=0""")
    for row in conn.execute('SELECT user_id,track_id,updated_at FROM playback_progress').fetchall():
        conn.execute('UPDATE playback_progress SET last_active_ms=? WHERE user_id=? AND track_id=?',
                     (timestamp_ms(row['updated_at']), row['user_id'], row['track_id']))
    conn.execute("""CREATE TABLE maintenance_state (
        name TEXT PRIMARY KEY, last_completed_ms INTEGER NOT NULL DEFAULT 0,
        lease_until_ms INTEGER NOT NULL DEFAULT 0, result_json TEXT)""")
    conn.execute('CREATE INDEX idx_tracks_retention ON tracks(last_used_ms,first_seen_ms)')
    conn.execute('CREATE INDEX idx_progress_activity ON playback_progress(last_active_ms)')
