from __future__ import annotations

import sqlite3
import threading
import time
import os
from pathlib import Path
from typing import Optional

from monitoring import record_database_operation


BASE_DIR = Path(__file__).resolve().parent


def resolve_data_dir(
    *,
    runtime: Optional[str] = None,
    configured_data_dir: Optional[str] = None,
    appdata: Optional[str] = None,
) -> Path:
    resolved_runtime = (runtime if runtime is not None else os.getenv("APP_RUNTIME", "")).strip().lower()
    explicit_data_dir = (
        configured_data_dir if configured_data_dir is not None else os.getenv("APP_DATA_DIR", "")
    ).strip()
    if explicit_data_dir:
        return Path(explicit_data_dir).expanduser()
    if resolved_runtime == "desktop":
        appdata_root = appdata or os.getenv("APPDATA")
        if appdata_root:
            return Path(appdata_root).expanduser() / "Bilibili Radio" / "data"
        return Path.home() / ".bilibili-radio" / "data"
    return BASE_DIR / "data"


DATA_DIR = resolve_data_dir()
DEFAULT_DB_PATH = DATA_DIR / "bili_radio.sqlite3"
SQLITE_BUSY_TIMEOUT_MS = 5_000
LEGACY_OWNER_USER_ID = "legacy-owner"

_init_lock = threading.Lock()
_initialized_paths: set[Path] = set()


class ClosingConnection(sqlite3.Connection):
    def __enter__(self):
        self._monitoring_started_at = time.perf_counter()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        outcome = "success"
        if exc_value is not None:
            message = str(exc_value).lower()
            outcome = "busy" if "locked" in message or "busy" in message else "error"
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        except sqlite3.Error as error:
            message = str(error).lower()
            outcome = "busy" if "locked" in message or "busy" in message else "error"
            raise
        finally:
            self.close()
            started_at = getattr(self, "_monitoring_started_at", None)
            if started_at is not None:
                record_database_operation(
                    "transaction",
                    outcome,
                    time.perf_counter() - started_at,
                )


def get_connection(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
        factory=ClosingConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute(f'PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}')
    conn.execute('PRAGMA synchronous = NORMAL')
    return conn


def init_db(db_path: Optional[Path | str] = None) -> None:
    path = (Path(db_path) if db_path else DEFAULT_DB_PATH).resolve()
    with _init_lock:
        if path in _initialized_paths and path.exists():
            return

        with get_connection(path) as conn:
            conn.execute('PRAGMA journal_mode = WAL')
            current_version = int(conn.execute('PRAGMA user_version').fetchone()[0])
            if current_version < 1:
                conn.executescript(
                    """
            CREATE TABLE IF NOT EXISTS tracks (
                track_id TEXT PRIMARY KEY,
                bvid TEXT NOT NULL,
                cid INTEGER,
                title TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT '',
                owner_mid INTEGER,
                cover TEXT NOT NULL DEFAULT '',
                duration INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                published_at TEXT,
                page INTEGER,
                page_title TEXT,
                source TEXT NOT NULL DEFAULT 'bili',
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tracks_bvid_cid
                ON tracks (bvid, cid);

            CREATE TABLE IF NOT EXISTS likes (
                track_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recent (
                track_id TEXT PRIMARY KEY,
                last_played_at TEXT NOT NULL,
                play_count INTEGER NOT NULL DEFAULT 1,
                position_ms INTEGER NOT NULL DEFAULT 0,
                listen_ms INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                cover TEXT,
                source_type TEXT NOT NULL DEFAULT 'user-created',
                source_bvid TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS playlist_items (
                playlist_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (playlist_id, track_id),
                FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS playback_sessions (
                session_id TEXT PRIMARY KEY,
                track_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                last_position_ms INTEGER NOT NULL DEFAULT 0,
                listen_ms INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                last_event TEXT NOT NULL,
                FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS playback_recent (
                track_id TEXT PRIMARY KEY,
                last_played_at TEXT NOT NULL,
                position_ms INTEGER NOT NULL DEFAULT 0,
                listen_ms INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS playback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                event TEXT NOT NULL,
                position_ms INTEGER NOT NULL DEFAULT 0,
                listen_ms INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_state (
                provider TEXT PRIMARY KEY,
                cookie_encrypted TEXT,
                refresh_token_encrypted TEXT,
                user_mid INTEGER,
                user_name TEXT,
                user_face TEXT,
                cookie_updated_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_qr_sessions (
                qrcode_key TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT
            );

            CREATE TABLE IF NOT EXISTS analysis_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                track_id TEXT,
                session_id TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS player_queue_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_index INTEGER NOT NULL DEFAULT -1,
                play_mode TEXT NOT NULL DEFAULT 'order',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS player_queue_items (
                position INTEGER PRIMARY KEY,
                track_id TEXT NOT NULL,
                added_at TEXT NOT NULL,
                FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
            );
                    """
                )
                conn.execute('PRAGMA user_version = 1')
                current_version = 1

            if current_version < 2:
                conn.executescript(
                    """
                    CREATE INDEX IF NOT EXISTS idx_recent_last_played_at
                        ON recent (last_played_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_likes_created_at
                        ON likes (created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_playlists_created_at
                        ON playlists (created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_playback_recent_last_played_at
                        ON playback_recent (last_played_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_playback_sessions_track_started
                        ON playback_sessions (track_id, started_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_playback_events_track_created
                        ON playback_events (track_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_playback_events_session_id
                        ON playback_events (session_id, id);
                    CREATE INDEX IF NOT EXISTS idx_analysis_events_event_created
                        ON analysis_events (event, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_analysis_events_track_created
                        ON analysis_events (track_id, created_at DESC);
                    """
                )
                conn.execute('PRAGMA user_version = 2')
                current_version = 2

            if current_version < 3:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_playlist_items_playlist_position
                        ON playlist_items (playlist_id, position)
                    """
                )
                conn.execute('PRAGMA user_version = 3')
                current_version = 3

            if current_version < 4:
                conn.commit()
                _migrate_to_v4(conn)
                current_version = 4

            if current_version < 5:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS track_reviews (
                        user_id TEXT NOT NULL,
                        track_id TEXT NOT NULL,
                        rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                        mood TEXT NOT NULL,
                        note TEXT NOT NULL DEFAULT '',
                        visibility TEXT NOT NULL DEFAULT 'private'
                            CHECK (visibility = 'private'),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (user_id, track_id),
                        FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                        FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_track_reviews_user_updated
                        ON track_reviews (user_id, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_track_reviews_track
                        ON track_reviews (track_id, updated_at DESC);
                    """
                )
                conn.execute('PRAGMA user_version = 5')
                current_version = 5

            if current_version < 6:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS recommendation_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        track_id TEXT NOT NULL,
                        event TEXT NOT NULL,
                        scene TEXT NOT NULL DEFAULT 'home',
                        source TEXT NOT NULL DEFAULT '',
                        reason TEXT NOT NULL DEFAULT '',
                        score REAL NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                        FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_recommendation_events_user_created
                        ON recommendation_events (user_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_recommendation_events_track_event
                        ON recommendation_events (user_id, track_id, event, created_at DESC);
                    """
                )
                conn.execute('PRAGMA user_version = 6')
                current_version = 6

            if current_version < 7:
                _add_column_if_missing(conn, "tracks", "owner_mid", "INTEGER")
                _add_column_if_missing(
                    conn,
                    "playlists",
                    "source_type",
                    "TEXT NOT NULL DEFAULT 'user-created'",
                )
                _add_column_if_missing(conn, "playlists", "source_bvid", "TEXT")
                conn.execute('PRAGMA user_version = 7')
                current_version = 7

            if current_version < 8:
                _ensure_recommendation_history_table(conn)
                conn.execute('PRAGMA user_version = 8')
                current_version = 8

            if current_version < 9:
                _add_column_if_missing(conn, "playback_sessions", "event_seq", "INTEGER NOT NULL DEFAULT 0")
                _add_column_if_missing(conn, "playback_sessions", "session_started_ms", "INTEGER NOT NULL DEFAULT 0")
                _add_column_if_missing(conn, "playback_sessions", "recent_counted", "INTEGER NOT NULL DEFAULT 0")
                conn.execute("UPDATE playback_sessions SET recent_counted = 1")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS playback_progress (
                        user_id TEXT NOT NULL,
                        track_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        session_started_ms INTEGER NOT NULL,
                        event_seq INTEGER NOT NULL,
                        position_ms INTEGER NOT NULL DEFAULT 0,
                        listen_ms INTEGER NOT NULL DEFAULT 0,
                        completed INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (user_id, track_id),
                        FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                        FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                    )
                """)
                conn.execute('PRAGMA user_version = 9')
                current_version = 9

            _ensure_current_schema_columns(conn)

        _initialized_paths.add(path)


def _ensure_current_schema_columns(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "tracks", "owner_mid", "INTEGER")
    _add_column_if_missing(
        conn,
        "playlists",
        "source_type",
        "TEXT NOT NULL DEFAULT 'user-created'",
    )
    _add_column_if_missing(conn, "playlists", "source_bvid", "TEXT")
    _ensure_recommendation_history_table(conn)


def _ensure_recommendation_history_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS recommendation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            recommended_at TEXT NOT NULL,
            clicked INTEGER NOT NULL DEFAULT 0,
            played_seconds INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            liked INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            scene TEXT NOT NULL DEFAULT 'home',
            source TEXT NOT NULL DEFAULT '',
            score REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
            FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_recommendation_history_user_recent
            ON recommendation_history (user_id, recommended_at DESC);
        CREATE INDEX IF NOT EXISTS idx_recommendation_history_track_recent
            ON recommendation_history (user_id, track_id, recommended_at DESC);
        CREATE INDEX IF NOT EXISTS idx_recommendation_history_feedback
            ON recommendation_history (user_id, track_id, skipped, completed, liked);
        """
    )


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    legacy_user_id = LEGACY_OWNER_USER_ID.replace("'", "''")
    conn.executescript(
        f"""
        PRAGMA foreign_keys = OFF;
        BEGIN IMMEDIATE;

        CREATE TABLE app_users (
            id TEXT PRIMARY KEY,
            oidc_issuer TEXT,
            oidc_subject TEXT,
            display_name TEXT NOT NULL DEFAULT '',
            email TEXT,
            avatar_url TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
            role_source TEXT NOT NULL DEFAULT 'local'
                CHECK (role_source IN ('local', 'oidc_group', 'bootstrap')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT,
            UNIQUE (oidc_issuer, oidc_subject)
        );

        INSERT INTO app_users (
            id, display_name, avatar_url, role, status, role_source,
            created_at, updated_at, last_login_at
        )
        VALUES (
            '{legacy_user_id}',
            COALESCE((SELECT user_name FROM auth_state WHERE provider = 'bilibili'), 'Legacy Owner'),
            COALESCE((SELECT user_face FROM auth_state WHERE provider = 'bilibili'), ''),
            'admin', 'active', 'bootstrap',
            COALESCE(
                (SELECT updated_at FROM auth_state WHERE provider = 'bilibili'),
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            COALESCE(
                (SELECT updated_at FROM auth_state WHERE provider = 'bilibili'),
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            NULL
        );

        CREATE TABLE app_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            revoked_at TEXT,
            ip_hash TEXT,
            user_agent_hash TEXT,
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
        );

        CREATE INDEX idx_app_sessions_user_expires
            ON app_sessions (user_id, expires_at DESC);

        CREATE TABLE admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT,
            request_id TEXT,
            details_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(actor_user_id) REFERENCES app_users(id) ON DELETE RESTRICT
        );

        CREATE INDEX idx_admin_audit_created
            ON admin_audit_log (created_at DESC);
        CREATE INDEX idx_admin_audit_actor_created
            ON admin_audit_log (actor_user_id, created_at DESC);

        CREATE TABLE bili_accounts (
            user_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL DEFAULT 'bilibili' CHECK (provider = 'bilibili'),
            cookie_encrypted TEXT,
            refresh_token_encrypted TEXT,
            user_mid INTEGER,
            user_name TEXT,
            user_face TEXT,
            cookie_updated_at TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
        );

        INSERT INTO bili_accounts (
            user_id, provider, cookie_encrypted, refresh_token_encrypted,
            user_mid, user_name, user_face, cookie_updated_at, updated_at
        )
        SELECT
            '{legacy_user_id}', provider, cookie_encrypted, refresh_token_encrypted,
            user_mid, user_name, user_face, cookie_updated_at, updated_at
        FROM auth_state
        WHERE provider = 'bilibili';

        CREATE TABLE likes_v4 (
            user_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, track_id),
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
            FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
        );
        INSERT INTO likes_v4 SELECT '{legacy_user_id}', track_id, created_at FROM likes;
        DROP TABLE likes;
        ALTER TABLE likes_v4 RENAME TO likes;

        CREATE TABLE recent_v4 (
            user_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            last_played_at TEXT NOT NULL,
            play_count INTEGER NOT NULL DEFAULT 1,
            position_ms INTEGER NOT NULL DEFAULT 0,
            listen_ms INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, track_id),
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
            FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
        );
        INSERT INTO recent_v4
            SELECT '{legacy_user_id}', track_id, last_played_at, play_count,
                   position_ms, listen_ms, completed
            FROM recent;
        DROP TABLE recent;
        ALTER TABLE recent_v4 RENAME TO recent;

        CREATE TABLE playlists_v4 (
            user_id TEXT NOT NULL,
            id TEXT NOT NULL,
            name TEXT NOT NULL,
            cover TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, id),
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
        );
        INSERT INTO playlists_v4
            SELECT '{legacy_user_id}', id, name, cover, created_at, updated_at
            FROM playlists;

        CREATE TABLE playlist_items_v4 (
            user_id TEXT NOT NULL,
            playlist_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (user_id, playlist_id, track_id),
            FOREIGN KEY(user_id, playlist_id)
                REFERENCES playlists_v4(user_id, id) ON DELETE CASCADE,
            FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
        );
        INSERT INTO playlist_items_v4
            SELECT '{legacy_user_id}', playlist_id, track_id, position, added_at
            FROM playlist_items;
        DROP TABLE playlist_items;
        DROP TABLE playlists;
        ALTER TABLE playlists_v4 RENAME TO playlists;
        ALTER TABLE playlist_items_v4 RENAME TO playlist_items;

        CREATE TABLE playback_sessions_v4 (
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            last_position_ms INTEGER NOT NULL DEFAULT 0,
            listen_ms INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            last_event TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id),
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
            FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
        );
        INSERT INTO playback_sessions_v4
            SELECT '{legacy_user_id}', session_id, track_id, started_at, ended_at,
                   last_position_ms, listen_ms, completed, skipped, last_event
            FROM playback_sessions;
        DROP TABLE playback_sessions;
        ALTER TABLE playback_sessions_v4 RENAME TO playback_sessions;

        CREATE TABLE playback_recent_v4 (
            user_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            last_played_at TEXT NOT NULL,
            position_ms INTEGER NOT NULL DEFAULT 0,
            listen_ms INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, track_id),
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
            FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
        );
        INSERT INTO playback_recent_v4
            SELECT '{legacy_user_id}', track_id, last_played_at, position_ms,
                   listen_ms, completed, skipped
            FROM playback_recent;
        DROP TABLE playback_recent;
        ALTER TABLE playback_recent_v4 RENAME TO playback_recent;

        CREATE TABLE playback_events_v4 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            event TEXT NOT NULL,
            position_ms INTEGER NOT NULL DEFAULT 0,
            listen_ms INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
            FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
        );
        INSERT INTO playback_events_v4
            SELECT id, '{legacy_user_id}', session_id, track_id, event, position_ms,
                   listen_ms, completed, created_at
            FROM playback_events;
        DROP TABLE playback_events;
        ALTER TABLE playback_events_v4 RENAME TO playback_events;

        CREATE TABLE settings_v4 (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key),
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
        );
        INSERT INTO settings_v4
            SELECT '{legacy_user_id}', key, value, updated_at FROM settings;
        DROP TABLE settings;
        ALTER TABLE settings_v4 RENAME TO settings;

        CREATE TABLE auth_qr_sessions_v4 (
            user_id TEXT NOT NULL,
            qrcode_key TEXT NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT,
            PRIMARY KEY (user_id, qrcode_key),
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
        );
        INSERT INTO auth_qr_sessions_v4
            SELECT '{legacy_user_id}', qrcode_key, url, status, message,
                   created_at, updated_at, expires_at
            FROM auth_qr_sessions;
        DROP TABLE auth_qr_sessions;
        ALTER TABLE auth_qr_sessions_v4 RENAME TO auth_qr_sessions;

        CREATE TABLE analysis_events_v4 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            event TEXT NOT NULL,
            track_id TEXT,
            session_id TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
        );
        INSERT INTO analysis_events_v4
            SELECT id, '{legacy_user_id}', event, track_id, session_id,
                   payload_json, created_at
            FROM analysis_events;
        DROP TABLE analysis_events;
        ALTER TABLE analysis_events_v4 RENAME TO analysis_events;

        CREATE TABLE player_queue_state_v4 (
            user_id TEXT PRIMARY KEY,
            current_index INTEGER NOT NULL DEFAULT -1,
            play_mode TEXT NOT NULL DEFAULT 'order',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
        );
        INSERT INTO player_queue_state_v4
            SELECT '{legacy_user_id}', current_index, play_mode, updated_at
            FROM player_queue_state WHERE id = 1;
        DROP TABLE player_queue_state;
        ALTER TABLE player_queue_state_v4 RENAME TO player_queue_state;

        CREATE TABLE player_queue_items_v4 (
            user_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            track_id TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (user_id, position),
            FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
            FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
        );
        INSERT INTO player_queue_items_v4
            SELECT '{legacy_user_id}', position, track_id, added_at
            FROM player_queue_items;
        DROP TABLE player_queue_items;
        ALTER TABLE player_queue_items_v4 RENAME TO player_queue_items;

        DROP TABLE auth_state;
        CREATE VIEW auth_state AS
            SELECT provider, cookie_encrypted, refresh_token_encrypted, user_mid,
                   user_name, user_face, cookie_updated_at, updated_at
            FROM bili_accounts
            WHERE user_id = '{legacy_user_id}';

        CREATE INDEX idx_recent_last_played_at
            ON recent (user_id, last_played_at DESC);
        CREATE INDEX idx_likes_created_at
            ON likes (user_id, created_at DESC);
        CREATE INDEX idx_playlists_created_at
            ON playlists (user_id, created_at DESC);
        CREATE INDEX idx_playlist_items_playlist_position
            ON playlist_items (user_id, playlist_id, position);
        CREATE INDEX idx_playback_recent_last_played_at
            ON playback_recent (user_id, last_played_at DESC);
        CREATE INDEX idx_playback_sessions_track_started
            ON playback_sessions (user_id, track_id, started_at DESC);
        CREATE INDEX idx_playback_events_track_created
            ON playback_events (user_id, track_id, created_at DESC);
        CREATE INDEX idx_playback_events_session_id
            ON playback_events (user_id, session_id, id);
        CREATE INDEX idx_analysis_events_event_created
            ON analysis_events (user_id, event, created_at DESC);
        CREATE INDEX idx_analysis_events_track_created
            ON analysis_events (user_id, track_id, created_at DESC);
        CREATE INDEX idx_auth_qr_sessions_user_created
            ON auth_qr_sessions (user_id, created_at DESC);

        """
    )

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        conn.rollback()
        conn.execute("PRAGMA foreign_keys = ON")
        raise sqlite3.IntegrityError(
            f"Tenant migration produced foreign key violations: {violations[:5]}"
        )
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
