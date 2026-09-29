import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from database import get_connection, init_db, _initialized_paths
from library_service import LibraryService
from metadata_maintenance import cleanup
from models import Track
from playback_service import PlaybackService
from queue_service import PlayerQueueService
from recommendation_service import RecommendationService
from retention import DAY_MS


def iso(stamp):
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat()


class RetentionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / 'data.sqlite3'
        self.library = LibraryService(self.db)
        self.player = PlaybackService(self.db)
        self.now = int(time.time() * 1000)
        self.track = Track(bvid='BV1Q541167Qg', cid=123, title='Episode', duration=900)
        self.library.upsert_track(self.track)

    def send(self, stamp, **changes):
        event = dict(sessionId='session', sessionStartedAtMs=self.now, trackId=self.track.track_id,
                     event='pause', positionMs=134000, listenMs=134000, eventSeq=1, lastActiveAtMs=stamp)
        event.update(changes)
        with patch('playback_progress.time.time', return_value=stamp/1000):
            return self.player.record_event(event)

    def resume(self, stamp):
        with patch('playback_progress.time.time', return_value=stamp/1000):
            return self.player.get_resume(self.track.track_id)

    def test_latest_pointer_rolls_forward_for_one_day(self):
        self.send(self.now)
        self.assertEqual(self.resume(self.now+23*3600000)['positionMs'],134000)
        later = self.now+23*3600000
        self.send(later, eventSeq=2, positionMs=261000, listenMs=261000)
        self.assertEqual(self.resume(later+DAY_MS-1)['positionMs'],261000)
        self.assertEqual(self.resume(later+DAY_MS)['positionMs'],0)

    def test_paused_events_and_delayed_uploads_do_not_renew_expiry(self):
        self.send(self.now)
        with patch('playback_progress.time.time',return_value=(self.now+DAY_MS-1000)/1000):
            self.player.record_event(dict(sessionId='session',trackId=self.track.track_id,event='quit',
                eventSeq=2,positionMs=134000,listenMs=134000,lastActiveAtMs=self.now))
        self.assertEqual(self.resume(self.now+DAY_MS)['positionMs'],0)
        with patch('playback_progress.time.time',return_value=(self.now+DAY_MS+1)/1000):
            result=self.player.record_event(dict(sessionId='session',trackId=self.track.track_id,event='heartbeat',
                eventSeq=3,positionMs=134000,listenMs=134000,lastActiveAtMs=self.now))
        self.assertFalse(result['accepted'])

    def test_legacy_identical_heartbeat_does_not_extend_active_time(self):
        self.send(self.now)
        with patch('playback_progress.time.time',return_value=(self.now+DAY_MS-1000)/1000):
            self.player.record_event(dict(sessionId='session',trackId=self.track.track_id,event='heartbeat',
                eventSeq=2,positionMs=134000,listenMs=134000))
        self.assertEqual(self.resume(self.now+DAY_MS)['positionMs'],0)

    def test_expired_legacy_history_cannot_resurrect_pointer(self):
        self.library.add_recent(self.track,position_ms=134000,listen_ms=134000)
        with get_connection(self.db) as conn:
            conn.execute('UPDATE recent SET last_played_at=?',(iso(self.now-2*DAY_MS),))
        self.assertEqual(self.resume(self.now)['positionMs'],0)
        self.assertEqual(self.library.list_recent()[0]['positionMs'],0)

    def test_cleanup_clears_positions_but_preserves_personal_history(self):
        self.library.add_like(self.track)
        self.send(self.now)
        result=cleanup(self.db,stamp=self.now+DAY_MS+100,force=True)
        self.assertEqual(result['playback_progress'],1)
        self.assertEqual(self.resume(self.now+DAY_MS+100)['positionMs'],0)
        with get_connection(self.db) as conn:
            row=conn.execute('SELECT * FROM recent').fetchone()
            self.assertEqual(row['position_ms'],0)
            self.assertEqual(row['listen_ms'],134000)
            self.assertEqual(row['play_count'],1)
            self.assertEqual(conn.execute('SELECT last_position_ms FROM playback_sessions').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM likes').fetchone()[0],1)

    def test_only_selected_recommendations_are_persisted(self):
        class Client:
            def search(self,*args,**kwargs):
                return [Track(bvid=f'BV{i:010d}',title=f'Candidate {i}') for i in range(30)]
        service=RecommendationService(self.db,bili_client=Client())
        result=service.list_recommendations()
        selected={item['track']['trackId'] for item in result['items']}
        with get_connection(self.db) as conn:
            saved={row[0] for row in conn.execute('SELECT track_id FROM tracks')}
        self.assertLessEqual(len(selected),8)
        self.assertEqual(saved,selected|{self.track.track_id})

    def test_feedback_does_not_count_as_exposure_and_survives_cleanup(self):
        service=RecommendationService(self.db)
        service.record_event({'trackId':self.track.track_id,'event':'dismissed'})
        profile=service._load_user_profile()
        self.assertNotIn(self.track.track_id,profile.recently_recommended_track_ids)
        self.assertIn(self.track.track_id,profile.skipped_track_ids)
        cleanup(self.db,stamp=self.now+40*DAY_MS,force=True)
        with get_connection(self.db) as conn:
            self.assertEqual(conn.execute('SELECT skipped FROM recommendation_feedback').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],1)

    def test_pure_exposures_expire_before_unreferenced_metadata(self):
        RecommendationService(self.db).record_event({'trackId':self.track.track_id,'event':'shown'})
        result=cleanup(self.db,stamp=self.now+15*DAY_MS,force=True)
        self.assertEqual(result['recommendation_events'],1)
        self.assertEqual(result['recommendation_history'],1)
        self.assertEqual(result['tracks'],0)
        self.assertEqual(cleanup(self.db,stamp=self.now+31*DAY_MS,force=True)['tracks'],1)

    def test_all_personal_references_are_protected_including_other_users(self):
        kinds=['likes','recent','playlist_items','player_queue_items','track_reviews','analysis_events','feedback','session']
        tracks=[Track(bvid='BV1Q541167Qg',cid=200+i,title=kind) for i,kind in enumerate(kinds)]
        self.library.upsert_tracks(tracks)
        with get_connection(self.db) as conn:
            conn.execute("INSERT INTO app_users(id,created_at,updated_at) VALUES ('other-user',?,?)",(iso(self.now),iso(self.now)))
        LibraryService(self.db,user_id='other-user').add_like(tracks[0])
        self.library.add_recent(tracks[1])
        playlist=self.library.create_playlist('protected')
        self.library.batch_add_playlist_items(playlist['id'],tracks=[tracks[2]])
        PlayerQueueService(self.db).save_queue([tracks[3]])
        self.library.save_review(tracks[4],rating=5,mood='test')
        with get_connection(self.db) as conn:
            conn.execute("INSERT INTO analysis_events(user_id,event,track_id,payload_json,created_at) VALUES ('legacy-owner','test',?,'{}',?)",(tracks[5].track_id,iso(self.now)))
        RecommendationService(self.db).record_event({'trackId':tracks[6].track_id,'event':'liked'})
        self.player.record_event({'trackId':tracks[7].track_id,'sessionId':'other','positionMs':5000,'listenMs':5000})
        result=cleanup(self.db,stamp=self.now+40*DAY_MS,force=True)
        self.assertEqual(result['tracks'],1)  # The unreferenced setUp track only.
        with get_connection(self.db) as conn:
            self.assertEqual({row[0] for row in conn.execute('SELECT track_id FROM tracks')},{t.track_id for t in tracks})

    def test_metadata_refresh_timestamp_does_not_extend_retention(self):
        with get_connection(self.db) as conn:
            conn.execute('UPDATE tracks SET first_seen_ms=?,last_used_ms=?,updated_at=?',
                         (self.now-40*DAY_MS,self.now-40*DAY_MS,iso(self.now)))
        self.assertEqual(cleanup(self.db,stamp=self.now,force=True)['tracks'],1)

    def test_daily_schedule_and_bounded_batches(self):
        self.library.upsert_tracks([Track(bvid=f'BV{i:010d}',title='Old') for i in range(5)])
        future=self.now+40*DAY_MS
        result=cleanup(self.db,stamp=future,batch_size=2,max_batches=1)
        self.assertEqual(result['tracks'],2)
        self.assertFalse(result['complete'])
        result=cleanup(self.db,stamp=future,batch_size=2)
        self.assertEqual(result['tracks'],4)
        self.assertTrue(result['complete'])
        self.assertTrue(cleanup(self.db,stamp=future+1000)['skipped'])

    def test_v9_migration_preserves_feedback_and_gives_legacy_metadata_grace(self):
        with get_connection(self.db) as conn:
            conn.execute("INSERT INTO recommendation_history(user_id,track_id,recommended_at,skipped) VALUES ('legacy-owner',?,?,1)",
                         (self.track.track_id,iso(self.now-40*DAY_MS)))
            conn.execute('DROP TRIGGER tracks_first_seen')
            conn.execute('DROP INDEX idx_tracks_retention')
            conn.execute('DROP INDEX idx_progress_activity')
            conn.execute('DROP TABLE recommendation_feedback')
            conn.execute('DROP TABLE maintenance_state')
            for column in ['first_seen_ms','last_used_ms','ingest_source']:
                conn.execute(f'ALTER TABLE tracks DROP COLUMN {column}')
            conn.execute('ALTER TABLE playback_progress DROP COLUMN last_active_ms')
            conn.execute('PRAGMA user_version=9')
        _initialized_paths.discard(self.db.resolve())
        init_db(self.db)
        with get_connection(self.db) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],10)
            self.assertEqual(conn.execute('SELECT skipped FROM recommendation_feedback').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM recommendation_history').fetchone()[0],0)
            self.assertGreaterEqual(conn.execute('SELECT first_seen_ms FROM tracks').fetchone()[0],self.now)
        self.assertEqual(cleanup(self.db,stamp=self.now+DAY_MS,force=True)['tracks'],0)
