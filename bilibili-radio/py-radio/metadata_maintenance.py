"""Bounded, transactional retention work inside the desktop backend process."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from database import DEFAULT_DB_PATH, get_connection, init_db
from retention import DAY_MS, EXPOSURE_TTL_MS, METADATA_TTL_MS, PROGRESS_TTL_MS, now_ms

log = logging.getLogger(__name__)


def cleanup(db_path=DEFAULT_DB_PATH, *, stamp=None, batch_size=200, max_batches=20, force=False):
    init_db(db_path)
    stamp = now_ms() if stamp is None else int(stamp)
    batch_size = max(1, min(int(batch_size), 500))
    result = {}
    with get_connection(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("INSERT OR IGNORE INTO maintenance_state(name) VALUES ('retention')")
        state = conn.execute("SELECT * FROM maintenance_state WHERE name='retention'").fetchone()
        if state['lease_until_ms'] > stamp or (not force and state['last_completed_ms'] > stamp - DAY_MS):
            return {'skipped': True}
        conn.execute("UPDATE maintenance_state SET lease_until_ms=? WHERE name='retention'", (stamp + 10 * 60_000,))
    try:
        # Only pure exposures are removed; personal feedback lives in a separate table.
        predicates = [
            ('playback_progress', 'last_active_ms <= ?', (stamp-PROGRESS_TTL_MS,)),
            ('recommendation_history', "CAST((julianday(recommended_at)-2440587.5)*86400000 AS INTEGER) <= ?", (stamp-EXPOSURE_TTL_MS,)),
            ('recommendation_events', "event='shown' AND CAST((julianday(created_at)-2440587.5)*86400000 AS INTEGER) <= ?", (stamp-EXPOSURE_TTL_MS,)),
        ]
        complete = True
        for table, predicate, args in predicates:
            count, done = _batches(db_path, table, predicate, args, batch_size, max_batches)
            result[table] = count
            complete &= done

        # Remove expired pointer copies, keeping listen totals, counts and history rows.
        for table, column, date in [
            ('recent','position_ms','last_played_at'),
            ('playback_recent','position_ms','last_played_at'),
            ('playback_sessions','last_position_ms','COALESCE(ended_at,started_at)'),
            ('playback_events','position_ms','created_at'),
        ]:
            predicate = f"{column} <> 0 AND CAST((julianday({date})-2440587.5)*86400000 AS INTEGER) <= ?"
            count, done = _batches(db_path, table, predicate, (stamp-PROGRESS_TTL_MS,), batch_size, max_batches,
                                   assignment=f'{column}=0')
            result[table+'_pointers'] = count
            complete &= done

        with get_connection(db_path) as conn:
            # Include every relation using track_id, even event tables without an FK.
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            references = [table for table in tables if table != 'tracks' and
                          any(row[1] == 'track_id' for row in conn.execute(f'PRAGMA table_info("{table}")'))]
        protected = ' AND '.join(f'NOT EXISTS (SELECT 1 FROM "{table}" r WHERE r.track_id=tracks.track_id)' for table in references)
        predicate = 'MAX(first_seen_ms,last_used_ms) <= ? AND ' + protected
        count, done = _batches(db_path, 'tracks', predicate, (stamp-METADATA_TTL_MS,), batch_size, max_batches)
        result['tracks'] = count
        complete &= done
        result['complete'] = complete
        with get_connection(db_path) as conn:
            conn.execute("""UPDATE maintenance_state SET lease_until_ms=0,
                last_completed_ms=CASE WHEN ? THEN ? ELSE last_completed_ms END,
                result_json=? WHERE name='retention'""", (int(complete),stamp,json.dumps(result)))
        return result
    except Exception:
        with get_connection(db_path) as conn:
            conn.execute("UPDATE maintenance_state SET lease_until_ms=0 WHERE name='retention'")
        raise


def _batches(db_path, table, predicate, args, batch_size, max_batches, assignment=None):
    count = 0
    for _ in range(max_batches):
        with get_connection(db_path) as conn:
            # Check references and delete in the same writer transaction. A concurrent
            # queue/like transaction either precedes this check or recreates metadata.
            conn.execute('BEGIN IMMEDIATE')
            operation = f'UPDATE "{table}" SET {assignment}' if assignment else f'DELETE FROM "{table}"'
            changed = conn.execute(f'{operation} WHERE rowid IN (SELECT rowid FROM "{table}" WHERE {predicate} LIMIT ?)',
                                   (*args, batch_size)).rowcount
        count += changed
        if changed < batch_size:
            return count, True
    return count, False


def start_desktop_maintenance(db_path=DEFAULT_DB_PATH, *, initial_delay=30):
    stop = threading.Event()

    def work():
        if stop.wait(initial_delay):
            return
        while not stop.is_set():
            try:
                result = cleanup(Path(db_path))
                if not result.get('skipped'):
                    log.info('Metadata maintenance: %s', result)
            except Exception:
                log.exception('Metadata maintenance deferred; will retry')
            # The persisted daily schedule also survives application restarts.
            if stop.wait(60):
                return

    thread = threading.Thread(target=work, name='metadata-maintenance', daemon=True)
    thread.start()
    return stop
