import sqlite3
import tempfile
import unittest
from pathlib import Path

from analysis_service import AnalysisService
from auth_service import AuthService
from constant import BilibiliAPI as APIConst
from database import LEGACY_OWNER_USER_ID, get_connection, init_db
from library_service import LibraryService
from models import Track
from playback_service import PlaybackService
from queue_service import PlayerQueueService
from settings_service import SettingsService


NOW = "2026-07-21T12:00:00+00:00"


def create_user(db_path: Path, user_id: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_users (
                id, display_name, role, status, role_source, created_at, updated_at
            ) VALUES (?, ?, 'user', 'active', 'local', ?, ?)
            """,
            (user_id, user_id, NOW, NOW),
        )


class FakeCookie:
    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value


class FakeResponse:
    def __init__(self, payload, cookies=None):
        self.status_code = 200
        self.reason = "OK"
        self._payload = payload
        self.cookies = cookies or []

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeAuthSession:
    def __init__(self, key: str, mid: int):
        self.headers = {}
        self.key = key
        self.mid = mid
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url == APIConst.QR_GENERATE_URL:
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "url": f"https://account.bilibili.com/scan?qrcode_key={self.key}",
                        "qrcode_key": self.key,
                    },
                }
            )
        if url == APIConst.QR_POLL_URL:
            return FakeResponse(
                {"code": 0, "data": {"code": 0, "refresh_token": f"rt-{self.mid}"}},
                cookies=[FakeCookie("SESSDATA", f"cookie-{self.mid}")],
            )
        if url == APIConst.NAV_URL:
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "isLogin": True,
                        "mid": self.mid,
                        "uname": f"User {self.mid}",
                        "face": f"//i0.hdslb.com/{self.mid}.jpg",
                        "level_info": {"current_level": 1},
                        "vip": {"type": 0},
                    },
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")


class TenantIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "tenant.sqlite3"
        init_db(self.db_path)
        create_user(self.db_path, "user-a")
        create_user(self.db_path, "user-b")
        self.track = Track(
            bvid="BV1xx411c7mD",
            cid=123,
            title="Shared Track",
            duration=100,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_schema_seeds_legacy_admin(self):
        with get_connection(self.db_path) as conn:
            owner = conn.execute(
                "SELECT role, role_source FROM app_users WHERE id = ?",
                (LEGACY_OWNER_USER_ID,),
            ).fetchone()
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()

        self.assertEqual(version, 9)
        self.assertEqual(owner["role"], "admin")
        self.assertEqual(owner["role_source"], "bootstrap")
        self.assertEqual(violations, [])

    def test_library_queue_settings_playback_and_analysis_are_isolated(self):
        library_a = LibraryService(self.db_path, user_id="user-a")
        library_b = LibraryService(self.db_path, user_id="user-b")

        library_a.add_like(self.track)
        library_a.add_recent(self.track, position_ms=25_000, listen_ms=20_000)
        library_a.save_review(self.track, rating=5, mood="治愈", note="private note")
        playlist_a = library_a.create_playlist("Private A", tracks=[self.track])

        self.assertEqual(library_b.list_likes(), [])
        self.assertEqual(library_b.list_recent(), [])
        self.assertIsNone(library_b.get_review(self.track.bvid, cid=self.track.cid))
        self.assertEqual(library_b.list_playlists(), [])
        with self.assertRaises(Exception):
            library_b.get_playlist(playlist_a["id"])

        library_b.add_like(self.track)
        library_b.save_review(self.track, rating=3, mood="怀旧")
        self.assertEqual(len(library_a.list_likes()), 1)
        self.assertEqual(len(library_b.list_likes()), 1)
        self.assertEqual(library_a.get_review(self.track.bvid, cid=self.track.cid)["note"], "private note")
        self.assertEqual(library_b.get_review(self.track.bvid, cid=self.track.cid)["mood"], "怀旧")

        queue_a = PlayerQueueService(self.db_path, user_id="user-a")
        queue_b = PlayerQueueService(self.db_path, user_id="user-b")
        queue_a.save_queue([self.track], current_index=0, play_mode="loop")
        queue_b.save_queue([], play_mode="shuffle")
        self.assertEqual(queue_a.get_queue()["playMode"], "loop")
        self.assertEqual(queue_b.get_queue()["queue"], [])

        settings_a = SettingsService(self.db_path, user_id="user-a")
        settings_b = SettingsService(self.db_path, user_id="user-b")
        settings_a.set_audio_quality_preference("192k")
        self.assertEqual(settings_a.get_audio_quality_preference(), "192k")
        self.assertEqual(settings_b.get_audio_quality_preference(), "auto")

        playback_a = PlaybackService(self.db_path, user_id="user-a")
        playback_b = PlaybackService(self.db_path, user_id="user-b")
        playback_a.record_event(
            {
                "sessionId": "same-session-id",
                "trackId": self.track.track_id,
                "positionMs": 30_000,
                "listenMs": 20_000,
                "event": "pause",
            }
        )
        playback_b.record_event(
            {
                "sessionId": "same-session-id",
                "trackId": self.track.track_id,
                "positionMs": 5_000,
                "listenMs": 5_000,
                "event": "skip",
            }
        )
        self.assertEqual(len(playback_a.list_recent()), 1)
        self.assertEqual(playback_b.list_recent(), [])

        AnalysisService(self.db_path, user_id="user-a").record_event({"event": "search"})
        AnalysisService(self.db_path, user_id="user-b").record_event({"event": "play"})
        with get_connection(self.db_path) as conn:
            sessions = conn.execute(
                "SELECT user_id FROM playback_sessions WHERE session_id = ? ORDER BY user_id",
                ("same-session-id",),
            ).fetchall()
            events = conn.execute(
                "SELECT user_id, event FROM analysis_events ORDER BY user_id"
            ).fetchall()
        self.assertEqual([row["user_id"] for row in sessions], ["user-a", "user-b"])
        self.assertEqual(
            [(row["user_id"], row["event"]) for row in events],
            [("user-a", "search"), ("user-b", "play")],
        )

    def test_bilibili_account_and_qr_poll_are_isolated(self):
        session_a = FakeAuthSession("key-a", 101)
        session_b = FakeAuthSession("key-b", 202)
        auth_a = AuthService(self.db_path, session=session_a, user_id="user-a")
        auth_b = AuthService(self.db_path, session=session_b, user_id="user-b")

        qr_a = auth_a.create_qrcode()
        before = len(session_b.calls)
        with self.assertRaises(Exception):
            auth_b.poll_qrcode(qr_a["qrcodeKey"])
        self.assertEqual(len(session_b.calls), before)

        auth_a.poll_qrcode(qr_a["qrcodeKey"])
        self.assertTrue(auth_a.get_status()["isLoggedIn"])
        self.assertFalse(auth_b.get_status()["isLoggedIn"])

        qr_b = auth_b.create_qrcode()
        auth_b.poll_qrcode(qr_b["qrcodeKey"])
        self.assertEqual(auth_a.get_status()["user"]["mid"], 101)
        self.assertEqual(auth_b.get_status()["user"]["mid"], 202)
        self.assertNotEqual(auth_a.get_cookie_header(), auth_b.get_cookie_header())


class LegacyMigrationTests(unittest.TestCase):
    def test_v3_rows_are_preserved_under_legacy_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.sqlite3"
            self._create_v3_database(db_path)

            init_db(db_path)

            with get_connection(db_path) as conn:
                scoped_tables = [
                    "likes",
                    "recent",
                    "playlists",
                    "playlist_items",
                    "playback_sessions",
                    "playback_recent",
                    "playback_events",
                    "settings",
                    "auth_qr_sessions",
                    "analysis_events",
                    "player_queue_state",
                    "player_queue_items",
                ]
                owners = {
                    table: conn.execute(
                        f"SELECT DISTINCT user_id FROM {table}"
                    ).fetchone()["user_id"]
                    for table in scoped_tables
                }
                account = conn.execute(
                    "SELECT user_mid, user_name FROM bili_accounts WHERE user_id = ?",
                    (LEGACY_OWNER_USER_ID,),
                ).fetchone()
                compatibility = conn.execute(
                    "SELECT user_mid FROM auth_state"
                ).fetchone()
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()

            self.assertEqual(set(owners.values()), {LEGACY_OWNER_USER_ID})
            self.assertEqual(account["user_mid"], 42)
            self.assertEqual(account["user_name"], "Legacy Bili User")
            self.assertEqual(compatibility["user_mid"], 42)
            self.assertEqual(violations, [])
            self.assertEqual(len(LibraryService(db_path).list_likes()), 1)
            self.assertEqual(PlayerQueueService(db_path).get_queue()["currentIndex"], 0)

    @staticmethod
    def _create_v3_database(db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE tracks (
                    track_id TEXT PRIMARY KEY, bvid TEXT NOT NULL, cid INTEGER,
                    title TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
                    cover TEXT NOT NULL DEFAULT '', duration INTEGER NOT NULL DEFAULT 0,
                    play_count INTEGER NOT NULL DEFAULT 0, published_at TEXT,
                    page INTEGER, page_title TEXT, source TEXT NOT NULL DEFAULT 'bili',
                    raw_json TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE likes (
                    track_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                );
                CREATE TABLE recent (
                    track_id TEXT PRIMARY KEY, last_played_at TEXT NOT NULL,
                    play_count INTEGER NOT NULL DEFAULT 1, position_ms INTEGER NOT NULL DEFAULT 0,
                    listen_ms INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                );
                CREATE TABLE playlists (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, cover TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE playlist_items (
                    playlist_id TEXT NOT NULL, track_id TEXT NOT NULL,
                    position INTEGER NOT NULL, added_at TEXT NOT NULL,
                    PRIMARY KEY (playlist_id, track_id),
                    FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                    FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                );
                CREATE TABLE playback_sessions (
                    session_id TEXT PRIMARY KEY, track_id TEXT NOT NULL,
                    started_at TEXT NOT NULL, ended_at TEXT,
                    last_position_ms INTEGER NOT NULL DEFAULT 0,
                    listen_ms INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0, last_event TEXT NOT NULL,
                    FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                );
                CREATE TABLE playback_recent (
                    track_id TEXT PRIMARY KEY, last_played_at TEXT NOT NULL,
                    position_ms INTEGER NOT NULL DEFAULT 0, listen_ms INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0, skipped INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                );
                CREATE TABLE playback_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    track_id TEXT NOT NULL, event TEXT NOT NULL,
                    position_ms INTEGER NOT NULL DEFAULT 0, listen_ms INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                );
                CREATE TABLE settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE auth_state (
                    provider TEXT PRIMARY KEY, cookie_encrypted TEXT,
                    refresh_token_encrypted TEXT, user_mid INTEGER, user_name TEXT,
                    user_face TEXT, cookie_updated_at TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE auth_qr_sessions (
                    qrcode_key TEXT PRIMARY KEY, url TEXT NOT NULL, status TEXT NOT NULL,
                    message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    expires_at TEXT
                );
                CREATE TABLE analysis_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL,
                    track_id TEXT, session_id TEXT, payload_json TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE player_queue_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_index INTEGER NOT NULL DEFAULT -1,
                    play_mode TEXT NOT NULL DEFAULT 'order', updated_at TEXT NOT NULL
                );
                CREATE TABLE player_queue_items (
                    position INTEGER PRIMARY KEY, track_id TEXT NOT NULL, added_at TEXT NOT NULL,
                    FOREIGN KEY(track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
                );

                INSERT INTO tracks VALUES (
                    'track-1', 'BV1xx411c7mD', 1, 'Legacy Track', '', '', 60, 0,
                    NULL, 1, 'P1', 'bili', NULL, '2026-07-20T00:00:00+00:00'
                );
                INSERT INTO likes VALUES ('track-1', '2026-07-20T00:00:00+00:00');
                INSERT INTO recent VALUES (
                    'track-1', '2026-07-20T00:00:00+00:00', 2, 1000, 2000, 0
                );
                INSERT INTO playlists VALUES (
                    'pl-1', 'Legacy List', NULL,
                    '2026-07-20T00:00:00+00:00', '2026-07-20T00:00:00+00:00'
                );
                INSERT INTO playlist_items VALUES (
                    'pl-1', 'track-1', 0, '2026-07-20T00:00:00+00:00'
                );
                INSERT INTO playback_sessions VALUES (
                    'session-1', 'track-1', '2026-07-20T00:00:00+00:00', NULL,
                    1000, 2000, 0, 0, 'play'
                );
                INSERT INTO playback_recent VALUES (
                    'track-1', '2026-07-20T00:00:00+00:00', 1000, 2000, 0, 0
                );
                INSERT INTO playback_events VALUES (
                    1, 'session-1', 'track-1', 'play', 1000, 2000, 0,
                    '2026-07-20T00:00:00+00:00'
                );
                INSERT INTO settings VALUES (
                    'audio_quality_preference', 'high', '2026-07-20T00:00:00+00:00'
                );
                INSERT INTO auth_state VALUES (
                    'bilibili', 'encrypted-cookie', 'encrypted-refresh', 42,
                    'Legacy Bili User', 'face.jpg', '2026-07-20T00:00:00+00:00',
                    '2026-07-20T00:00:00+00:00'
                );
                INSERT INTO auth_qr_sessions VALUES (
                    'legacy-key', 'https://example.test/qr', 'waiting', NULL,
                    '2026-07-20T00:00:00+00:00', '2026-07-20T00:00:00+00:00', NULL
                );
                INSERT INTO analysis_events VALUES (
                    1, 'play', 'track-1', 'session-1', '{}',
                    '2026-07-20T00:00:00+00:00'
                );
                INSERT INTO player_queue_state VALUES (
                    1, 0, 'loop', '2026-07-20T00:00:00+00:00'
                );
                INSERT INTO player_queue_items VALUES (
                    0, 'track-1', '2026-07-20T00:00:00+00:00'
                );
                PRAGMA user_version = 3;
                """
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
