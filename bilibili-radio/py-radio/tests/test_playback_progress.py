import tempfile
import unittest
from pathlib import Path

from database import get_connection
from error_code import APIError
from models import Track
from playback_service import PlaybackService


class PlaybackProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'playback.sqlite3'
        self.service = PlaybackService(self.db)
        self.track = Track(bvid='BV1Q541167Qg', cid=123, title='Long episode', duration=600)
        self.service.library.upsert_track(self.track)

    def send(self, **changes):
        payload = dict(sessionId='session-a', trackId=self.track.track_id, event='heartbeat',
                       eventSeq=1, sessionStartedAtMs=1000, positionMs=5000, listenMs=5000)
        payload.update(changes)
        return self.service.record_event(payload)

    def test_short_pause_survives_service_restart_without_entering_recent(self):
        self.send(event='pause')
        resumed = PlaybackService(self.db).get_resume(self.track.track_id)
        self.assertEqual(resumed['positionMs'], 5000)
        self.assertEqual(self.service.list_recent(), [])

    def test_backwards_seek_saves_actual_position_and_rejects_late_event(self):
        self.send(positionMs=120000, listenMs=70000)
        self.send(eventSeq=3, event='seek', positionMs=50000, listenMs=70000)
        result = self.send(eventSeq=2, positionMs=125000, listenMs=65000)
        self.assertFalse(result['accepted'])
        self.assertEqual(self.service.get_resume(self.track.track_id)['positionMs'], 50000)

    def test_previous_session_cannot_overwrite_a_new_session(self):
        self.send()
        self.send(sessionId='session-b', sessionStartedAtMs=2000, positionMs=20000)
        self.send(eventSeq=10, positionMs=100000)
        self.assertEqual(self.service.get_resume(self.track.track_id)['positionMs'], 20000)

    def test_heartbeats_and_retries_count_once_per_session(self):
        self.send(positionMs=60000, listenMs=60000)
        self.send(eventSeq=2, positionMs=65000, listenMs=65000)
        self.send(eventSeq=2, positionMs=65000, listenMs=65000)
        history = self.service.library.list_recent()[0]
        self.assertEqual(history['recentPlayCount'], 1)
        self.assertEqual(history['positionMs'], 65000)
        self.send(sessionId='session-b', sessionStartedAtMs=2000, positionMs=70000, listenMs=70000)
        self.assertEqual(self.service.library.list_recent()[0]['recentPlayCount'], 2)

    def test_statistical_completion_does_not_discard_the_end_of_episode(self):
        self.send(positionMs=550000, listenMs=10000)
        self.assertFalse(self.service.get_resume(self.track.track_id)['completed'])
        self.send(eventSeq=2, event='ended', positionMs=600000, listenMs=60000)
        self.assertTrue(self.service.get_resume(self.track.track_id)['completed'])

    def test_legacy_library_history_is_resumable(self):
        self.service.library.add_recent(self.track, position_ms=45000, listen_ms=45000)
        self.assertEqual(self.service.get_resume(self.track.track_id)['positionMs'], 45000)

    def test_short_replay_updates_visible_history_position_without_counting_again(self):
        self.service.library.add_recent(self.track, position_ms=45000, listen_ms=45000)
        self.send(event='pause', positionMs=50000, listenMs=5000)
        history = self.service.library.list_recent()[0]
        self.assertEqual(history['positionMs'], 50000)
        self.assertEqual(history['recentPlayCount'], 1)

    def test_progress_is_scoped_by_episode_and_user(self):
        self.send()
        other = Track(bvid=self.track.bvid, cid=456, title='Another episode', duration=600)
        self.service.library.upsert_track(other)
        self.assertEqual(self.service.get_resume(other.track_id)['positionMs'], 0)
        # Reading another identity must not expose the owner's history or checkpoint.
        self.assertEqual(PlaybackService(self.db, user_id='another-user').get_resume(self.track.track_id)['positionMs'], 0)

    def test_checkpoint_can_create_resolved_track_before_queue_debounce(self):
        track = Track(bvid=self.track.bvid, cid=999, title='New episode', duration=600)
        self.send(trackId=track.track_id, track=track.to_dict())
        self.assertEqual(self.service.get_resume(track.track_id)['positionMs'], 5000)

    def test_a_session_cannot_change_its_track(self):
        self.send()
        other = Track(bvid=self.track.bvid, cid=888, title='Other', duration=600)
        self.service.library.upsert_track(other)
        with self.assertRaises(APIError):
            self.send(trackId=other.track_id, eventSeq=2)

    def test_invalid_position_is_a_validation_error(self):
        with self.assertRaises(APIError):
            self.send(positionMs='not-a-number')

    def test_schema_is_v9_with_foreign_keys_intact(self):
        self.send()
        with get_connection(self.db) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 10)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
