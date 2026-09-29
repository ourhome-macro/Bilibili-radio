import threading
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask

from analysis_service import AnalysisService
from auth_service import AuthService
from bili_client import BiliClient
from constant import BilibiliAPI as APIConst
from database import get_connection
from error_code import APIError
from library_service import LibraryService
from models import AudioStreamInfo, Track, make_track_id
from playback_service import PlaybackService
from queue_service import PlayerQueueService
from recommendation_service import RecommendationService
from settings_service import SettingsService
from stream_service import StreamService
from track_service import (
    cover_info_from_video_data,
    is_valid_subtitle_url,
    normalize_player_chapters,
    normalize_player_subtitles,
    normalize_reply_comments,
    normalize_search_item,
    normalize_space_archive_item,
    normalize_subtitle_lines,
    normalize_video_detail,
    normalize_video_intro,
    parse_duration,
)


VALID_BVID = "BV1Q541167Qg"


class TrackServiceTests(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(parse_duration("03:25"), 205)
        self.assertEqual(parse_duration("01:02:03"), 3723)
        self.assertEqual(parse_duration(245), 245)

    def test_normalize_search_item(self):
        track = normalize_search_item(
            {
                "bvid": VALID_BVID,
                "title": "<em class=\"keyword\">Hello</em> World",
                "author": "tester",
                "pic": "//i0.hdslb.com/test.jpg",
                "duration": "04:05",
                "play": 123,
                "pubdate": 1784541600,
            }
        )

        self.assertEqual(track.bvid, VALID_BVID)
        self.assertEqual(track.title, "Hello World")
        self.assertEqual(track.cover, "https://i0.hdslb.com/test.jpg")
        self.assertEqual(track.duration, 245)
        self.assertEqual(track.play_count, 123)
        self.assertTrue(track.published_at.endswith("+08:00"))

    def test_video_detail_uses_page_first_frame_as_track_cover(self):
        detail = normalize_video_detail(
            {
                "bvid": VALID_BVID,
                "cid": 1,
                "title": "Multi Part",
                "pic": "http://i0.hdslb.com/video.jpg",
                "owner": {"name": "UP"},
                "pages": [
                    {"cid": 1, "page": 1, "part": "P1", "duration": 60, "first_frame": "//i0.hdslb.com/p1.jpg"},
                    {"cid": 2, "page": 2, "part": "P2", "duration": 60},
                ],
            }
        )

        self.assertEqual(detail.pages[0].cover, "https://i0.hdslb.com/p1.jpg")
        self.assertEqual(detail.pages[1].cover, "https://i0.hdslb.com/video.jpg")

    def test_space_archive_length_becomes_track_duration(self):
        track = normalize_space_archive_item(
            {
                "bvid": VALID_BVID,
                "title": "Space archive",
                "length": "03:45",
                "pic": "//i0.hdslb.com/archive.jpg",
                "play": 10,
            },
            {"mid": 12345, "name": "UP"},
        )

        self.assertIsNotNone(track)
        self.assertEqual(track.duration, 225)
        self.assertEqual(track.owner_mid, 12345)


class FakeCookie:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class FakeResponse:
    def __init__(self, status_code=200, payload=None, reason="OK", cookies=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.reason = reason
        self.headers = {"content-type": "application/json"}
        self.cookies = cookies or []

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            error = requests.HTTPError(f"{self.status_code} {self.reason}")
            error.response = self
            raise error


class FakeSearchSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url.endswith("bilibili.com/"):
            return FakeResponse()
        if len([call for call in self.calls if "search/type" in call]) == 1:
            return FakeResponse(status_code=412, payload={"code": -412, "message": "request was banned"}, reason="Precondition Failed")
        return FakeResponse(
            payload={
                "code": 0,
                "data": {
                    "result": [
                        {
                            "bvid": VALID_BVID,
                            "title": "Search Result",
                            "author": "UP",
                            "pic": "//i0.hdslb.com/a.jpg",
                            "duration": "01:02",
                            "play": 9,
                        }
                    ]
                },
            }
        )


class BiliClientTests(unittest.TestCase):
    def test_search_warms_guest_cookie_and_retries_412_once(self):
        client = BiliClient()
        client.session = FakeSearchSession()

        tracks = client.search("lofi", page=1, page_size=1)

        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].bvid, VALID_BVID)
        self.assertEqual(client.session.calls.count(BiliClient.HOME_URL), 2)
        self.assertEqual(len([call for call in client.session.calls if "search/type" in call]), 2)

    def test_quality_selection_falls_back_to_available_stream(self):
        streams = [
            {"id": 30216, "bandwidth": 64000},
            {"id": 30232, "bandwidth": 128000},
        ]

        selected = BiliClient._select_audio_stream(streams, "high")

        self.assertEqual(selected["id"], 30232)

    def test_favorite_tracks_are_normalized_and_authenticated(self):
        class FavoriteSession:
            def __init__(self):
                self.headers = {}
                self.last_headers = None

            def get(self, url, **kwargs):
                self.last_headers = kwargs.get("headers")
                if url != APIConst.FAVORITE_RESOURCE_URL:
                    raise AssertionError(f"Unexpected URL: {url}")
                return FakeResponse(
                    payload={
                        "code": 0,
                        "data": {
                            "info": {
                                "id": 12,
                                "title": "Fav",
                                "media_count": 2,
                                "cover": "//i0.hdslb.com/fav.jpg",
                            },
                            "has_more": False,
                            "medias": [
                                {
                                    "bvid": VALID_BVID,
                                    "title": "Fav Track",
                                    "cover": "//i0.hdslb.com/cover.jpg",
                                    "duration": 62,
                                    "upper": {"name": "UP"},
                                    "cnt_info": {"play": 9},
                                    "pubtime": 1784541600,
                                },
                                {"title": "Unavailable"},
                            ],
                        },
                    }
                )

        session = FavoriteSession()
        client = BiliClient(cookie_provider=lambda: "SESSDATA=abc")
        client.session = session

        result = client.list_favorite_tracks(12)

        self.assertEqual(result["folder"]["title"], "Fav")
        self.assertEqual(result["tracks"][0]["bvid"], VALID_BVID)
        self.assertEqual(result["tracks"][0]["cover"], "https://i0.hdslb.com/cover.jpg")
        self.assertEqual(result["unavailable"], 1)
        self.assertEqual(session.last_headers["Cookie"], "SESSDATA=abc")

    def test_resolve_bvid_cid_rejects_foreign_cid(self):
        client = BiliClient()
        client._get_video_detail_payload = lambda _bvid: {
            "bvid": VALID_BVID,
            "cid": 111,
            "pages": [{"cid": 111}, {"cid": 222}],
        }

        self.assertEqual(client._resolve_bvid_cid(VALID_BVID, 222), (VALID_BVID, 222))
        with self.assertRaises(APIError):
            client._resolve_bvid_cid(VALID_BVID, 999)


class LibraryServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.service = LibraryService(self.db_path)
        self.track = Track(
            bvid=VALID_BVID,
            cid=123,
            title="Test Track",
            owner="UP",
            duration=100,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_likes_recent_and_playlist_survive_new_service_instance(self):
        self.service.add_like(self.track)
        self.service.add_recent(self.track, position_ms=42_000, listen_ms=20_000)
        playlist = self.service.create_playlist("Inbox", tracks=[self.track])

        reloaded = LibraryService(self.db_path)
        self.assertEqual(len(reloaded.list_likes()), 1)
        self.assertEqual(reloaded.list_recent()[0]["positionMs"], 42_000)
        self.assertEqual(reloaded.get_playlist(playlist["id"])["tracks"][0]["trackId"], self.track.track_id)

    def test_batch_preview_and_add_deduplicates(self):
        playlist = self.service.create_playlist("Batch")
        preview = self.service.preview_playlist_items(
            playlist["id"],
            tracks=[self.track, self.track],
            track_ids=["missing"],
        )
        self.assertEqual(preview["total"], 3)
        self.assertEqual(preview["added"], 1)
        self.assertEqual(preview["duplicated"], 1)
        self.assertEqual(preview["unavailable"], 1)

        result = self.service.batch_add_playlist_items(playlist["id"], tracks=[self.track, self.track])
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["duplicated"], 1)
        self.assertEqual(len(self.service.get_playlist(playlist["id"])["tracks"]), 1)

    def test_batch_add_two_new_tracks_uses_one_write_transaction(self):
        playlist = self.service.create_playlist("Two tracks")
        tracks = [
            Track(bvid=VALID_BVID, cid=1001, title="Track 1"),
            Track(bvid=VALID_BVID, cid=1002, title="Track 2"),
        ]

        result = self.service.batch_add_playlist_items(playlist["id"], tracks=tracks)

        self.assertEqual(result["added"], 2)
        self.assertEqual(len(self.service.get_playlist(playlist["id"])["tracks"]), 2)

    def test_batch_add_one_hundred_new_tracks(self):
        playlist = self.service.create_playlist("One hundred tracks")
        tracks = [
            Track(bvid=VALID_BVID, cid=2000 + index, title=f"Track {index}")
            for index in range(100)
        ]

        result = self.service.batch_add_playlist_items(playlist["id"], tracks=tracks)

        self.assertEqual(result["added"], 100)
        stored = self.service.get_playlist(playlist["id"])["tracks"]
        self.assertEqual(len(stored), 100)
        self.assertEqual([track["cid"] for track in stored], list(range(2000, 2100)))

    def test_database_enables_wal_busy_timeout_and_query_indexes(self):
        with get_connection(self.db_path) as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }

        self.assertEqual(journal_mode, "wal")
        self.assertGreaterEqual(busy_timeout, 5_000)
        self.assertGreaterEqual(user_version, 3)
        self.assertIn("idx_recent_last_played_at", indexes)
        self.assertIn("idx_playback_events_track_created", indexes)
        self.assertIn("idx_playlist_items_playlist_position", indexes)

    def test_clear_recent_removes_recent_rows(self):
        self.service.add_recent(self.track, position_ms=42_000, listen_ms=20_000)
        self.assertEqual(len(self.service.list_recent()), 1)

        result = self.service.clear_recent()

        self.assertEqual(result["removed"], 1)
        self.assertEqual(self.service.list_recent(), [])

    def test_remove_recent_removes_only_target_track(self):
        other = Track(
            bvid=VALID_BVID,
            cid=456,
            title="Other Track",
            owner="UP",
            duration=120,
        )
        self.service.add_recent(self.track, position_ms=42_000, listen_ms=20_000)
        self.service.add_recent(other, position_ms=12_000, listen_ms=20_000)

        result = self.service.remove_recent(self.track.bvid, cid=self.track.cid)
        remaining = self.service.list_recent()

        self.assertEqual(result["removed"], 1)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["trackId"], other.track_id)

    def test_private_review_roundtrip_updates_and_deletes(self):
        review = self.service.save_review(
            self.track,
            rating=4,
            mood="平静",
            note="适合晚上听",
        )

        self.assertEqual(review["rating"], 4)
        self.assertEqual(review["mood"], "平静")
        self.assertEqual(review["note"], "适合晚上听")
        self.assertEqual(review["visibility"], "private")
        self.assertEqual(
            self.service.get_review(self.track.bvid, cid=self.track.cid)["trackId"],
            self.track.track_id,
        )

        updated = self.service.save_review(self.track, rating=5, mood="治愈")
        self.assertEqual(updated["rating"], 5)
        self.assertEqual(updated["note"], "")

        deleted = self.service.delete_review(self.track.bvid, cid=self.track.cid)
        self.assertTrue(deleted["deleted"])
        self.assertIsNone(self.service.get_review(self.track.bvid, cid=self.track.cid))

    def test_private_review_validates_required_fields(self):
        with self.assertRaises(APIError):
            self.service.save_review(self.track, rating=0, mood="平静")
        with self.assertRaises(APIError):
            self.service.save_review(self.track, rating=3, mood="")


class FakeRecommendationBiliClient:
    def __init__(self, up_tracks=None, search_tracks=None):
        self.up_tracks = up_tracks or {}
        self.search_tracks = search_tracks or []
        self.search_keywords = []

    def list_user_tracks(self, mid, page=1, page_size=20, order="pubdate"):
        return {
            "mid": mid,
            "page": page,
            "pageSize": page_size,
            "order": order,
            "total": len(self.up_tracks.get(mid, [])),
            "hasMore": False,
            "profile": {"mid": mid, "name": f"UP {mid}", "face": "", "description": ""},
            "tracks": [track.to_dict() for track in self.up_tracks.get(mid, [])],
        }

    def search(self, keyword, page=1, page_size=20):
        self.search_keywords.append(keyword)
        return self.search_tracks[:page_size]


class RecommendationServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.library = LibraryService(self.db_path)
        self.high_signal = Track(bvid=VALID_BVID, cid=301, title="High Signal", owner="UP", owner_mid=100)
        self.low_signal = Track(bvid=VALID_BVID, cid=302, title="Low Signal", owner="UP", owner_mid=200)
        self.related_up = Track(bvid=VALID_BVID, cid=303, title="Related UP", owner="UP", owner_mid=100)
        self.tag_peer = Track(bvid=VALID_BVID, cid=304, title="Tag Peer", owner="Other", owner_mid=300)
        self.popular = Track(bvid=VALID_BVID, cid=305, title="Popular Music", owner="Hot", owner_mid=400)
        self.fake_bili = FakeRecommendationBiliClient(
            up_tracks={100: [self.related_up], 200: []},
            search_tracks=[self.popular],
        )
        self.service = RecommendationService(self.db_path, bili_client=self.fake_bili)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reviews_and_likes_create_related_recommendations(self):
        self.library.add_recent(self.low_signal, listen_ms=20_000)
        self.library.add_recent(self.high_signal, listen_ms=20_000)
        self.library.add_like(self.high_signal)
        self.library.save_review(self.high_signal, rating=5, mood="治愈")
        self.library.save_review(self.tag_peer, rating=4, mood="治愈")

        result = self.service.list_recommendations(limit=5)
        track_ids = [item["track"]["trackId"] for item in result["items"]]

        self.assertIn(self.related_up.track_id, track_ids)
        self.assertIn(self.tag_peer.track_id, track_ids)
        self.assertTrue(
            any("常听 UP" in item["reason"] or "标签是" in item["reason"] for item in result["items"])
        )

    def test_home_recommendations_use_five_unheard_exploration_slots(self):
        search_tracks = [
            Track(bvid=VALID_BVID, cid=410 + index, title=f"Explore {index}", owner="Search", owner_mid=500 + index)
            for index in range(8)
        ]
        up_tracks = {
            100: [
                Track(bvid=VALID_BVID, cid=330 + index, title=f"UP Other {index}", owner="UP", owner_mid=100)
                for index in range(5)
            ]
        }
        self.fake_bili = FakeRecommendationBiliClient(up_tracks=up_tracks, search_tracks=search_tracks)
        self.service = RecommendationService(self.db_path, bili_client=self.fake_bili)
        self.library.add_recent(self.high_signal, listen_ms=20_000)
        self.library.save_review(self.high_signal, rating=5, mood="heal")

        result = self.service.list_recommendations(limit=8)
        explore_items = [item for item in result["items"] if item["source"] == "explore"]

        self.assertEqual(len(result["items"]), 8)
        self.assertEqual(len(explore_items), 5)
        self.assertTrue(any("heal" in keyword for keyword in self.fake_bili.search_keywords))
        self.assertTrue(all(item["track"]["trackId"] != self.high_signal.track_id for item in explore_items))

    def test_exploration_does_not_backfill_with_heard_tracks(self):
        self.fake_bili = FakeRecommendationBiliClient(
            up_tracks={100: []},
            search_tracks=[Track(bvid=VALID_BVID, cid=510, title="Only New", owner="Search", owner_mid=510)],
        )
        self.service = RecommendationService(self.db_path, bili_client=self.fake_bili)
        self.library.add_recent(self.high_signal, listen_ms=20_000)
        self.library.add_recent(self.low_signal, listen_ms=20_000)
        self.library.save_review(self.high_signal, rating=5, mood="heal")

        result = self.service.list_recommendations(limit=8)
        explore_items = [item for item in result["items"] if item["source"] == "explore"]

        self.assertEqual(len(explore_items), 1)
        self.assertEqual(explore_items[0]["track"]["title"], "Only New")
        self.assertLess(len(result["items"]), 8)

    def test_recommendation_feedback_is_persisted(self):
        self.library.add_recent(self.high_signal, listen_ms=20_000)
        event = self.service.record_event(
            {
                "trackId": self.high_signal.track_id,
                "event": "dismissed",
                "scene": "home",
                "source": "liked",
                "reason": "test",
                "score": 10,
            }
        )

        with get_connection(self.db_path) as conn:
            row = conn.execute("SELECT event, scene FROM recommendation_events").fetchone()
            history = conn.execute(
                "SELECT skipped FROM recommendation_feedback WHERE track_id = ?",
                (self.high_signal.track_id,),
            ).fetchone()

        self.assertEqual(event["event"], "dismissed")
        self.assertEqual(row["event"], "dismissed")
        self.assertEqual(row["scene"], "home")
        self.assertEqual(history["skipped"], 1)

    def test_recent_only_candidates_are_returned_as_cold_start_fallback(self):
        self.service = RecommendationService(self.db_path, bili_client=FakeRecommendationBiliClient())
        self.library.add_recent(self.low_signal, listen_ms=20_000)

        result = self.service.list_recommendations(limit=5)

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["track"]["trackId"], self.low_signal.track_id)


class PlayerQueueServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.service = PlayerQueueService(self.db_path)
        self.tracks = [
            Track(bvid=VALID_BVID, cid=123, title="P1", owner="UP", duration=100),
            Track(bvid=VALID_BVID, cid=456, title="P2", owner="UP", duration=120),
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def test_queue_snapshot_survives_new_service_instance(self):
        saved = self.service.save_queue(self.tracks, current_index=1, play_mode="loop")

        reloaded = PlayerQueueService(self.db_path).get_queue()

        self.assertEqual(saved["currentIndex"], 1)
        self.assertEqual(reloaded["playMode"], "loop")
        self.assertEqual([track["cid"] for track in reloaded["queue"]], [123, 456])

    def test_empty_queue_keeps_state_row_to_avoid_local_resurrection(self):
        self.service.save_queue(self.tracks, current_index=0, play_mode="shuffle")
        cleared = self.service.clear_queue()

        self.assertEqual(cleared["queue"], [])
        self.assertEqual(cleared["currentIndex"], -1)
        self.assertIsNotNone(cleared["updatedAt"])


class AuthServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_qr_poll_success_saves_encrypted_cookie_and_profile(self):
        class AuthSession:
            def __init__(self):
                self.headers = {}

            def get(self, url, **kwargs):
                if url == APIConst.QR_GENERATE_URL:
                    return FakeResponse(
                        payload={
                            "code": 0,
                            "data": {
                                "url": "https://account.bilibili.com/scan?qrcode_key=key1",
                                "qrcode_key": "key1",
                            },
                        }
                    )
                if url == APIConst.QR_POLL_URL:
                    return FakeResponse(
                        payload={"code": 0, "data": {"code": 0, "refresh_token": "rt1"}},
                        cookies=[
                            FakeCookie("SESSDATA", "abc"),
                            FakeCookie("DedeUserID", "123"),
                        ],
                    )
                if url == APIConst.NAV_URL:
                    return FakeResponse(
                        payload={
                            "code": 0,
                            "data": {
                                "isLogin": True,
                                "mid": 123,
                                "uname": "Tester",
                                "face": "//i0.hdslb.com/face.jpg",
                                "level_info": {"current_level": 5},
                                "vip": {"type": 2},
                            },
                        }
                    )
                raise AssertionError(f"Unexpected URL: {url}")

        service = AuthService(self.db_path, session=AuthSession())
        qr = service.create_qrcode()
        result = service.poll_qrcode(qr["qrcodeKey"])

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["user"]["mid"], 123)
        self.assertEqual(service.get_cookie_header(), "SESSDATA=abc; DedeUserID=123")
        with get_connection(self.db_path) as conn:
            row = conn.execute("SELECT cookie_encrypted FROM auth_state").fetchone()
        self.assertNotIn("SESSDATA=abc", row["cookie_encrypted"])


class SettingsServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.service = SettingsService(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_audio_quality_preference_persists(self):
        self.assertEqual(self.service.get_audio_quality_preference(), "auto")

        self.service.set_audio_quality_preference("192k")
        reloaded = SettingsService(self.db_path)

        self.assertEqual(reloaded.get_audio_quality_preference(), "192k")

    def test_audio_quality_preference_rejects_invalid_values(self):
        with self.assertRaises(Exception):
            self.service.set_audio_quality_preference("lossless")


class PlaybackServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.library = LibraryService(self.db_path)
        self.track = Track(bvid=VALID_BVID, cid=123, title="Playable", duration=100)
        self.library.upsert_track(self.track)
        self.service = PlaybackService(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_heartbeat_updates_recent_only_after_effective_listen(self):
        payload = {
            "sessionId": "s1",
            "trackId": self.track.track_id,
            "positionMs": 8_000,
            "listenMs": 9_000,
            "event": "heartbeat",
        }
        self.service.record_event(payload)
        self.assertEqual(self.service.list_recent(), [])

        payload["positionMs"] = 10_000
        payload["listenMs"] = 10_000
        self.service.record_event(payload)
        recent = self.service.list_recent()
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["trackId"], self.track.track_id)

    def test_completed_rule_uses_ninety_percent(self):
        result = self.service.record_event(
            {
                "sessionId": "s2",
                "trackId": self.track.track_id,
                "positionMs": 91_000,
                "listenMs": 50_000,
                "event": "end",
            }
        )
        self.assertTrue(result["completed"])

    def test_skip_under_effective_listen_does_not_enter_recent(self):
        result = self.service.record_event(
            {
                "sessionId": "s3",
                "trackId": self.track.track_id,
                "positionMs": 8_000,
                "listenMs": 8_000,
                "event": "skip",
            }
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(self.service.list_recent(), [])


class AnalysisServiceTests(unittest.TestCase):
    def test_record_event_persists_analysis_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            service = AnalysisService(db_path)
            result = service.record_event(
                {
                    "event": "favorite_imported",
                    "trackId": f"bili:{VALID_BVID}:cid:123",
                    "payload": {"added": 1},
                }
            )

            with get_connection(db_path) as conn:
                row = conn.execute("SELECT event, track_id FROM analysis_events").fetchone()

        self.assertGreater(result["id"], 0)
        self.assertEqual(row["event"], "favorite_imported")
        self.assertEqual(row["track_id"], f"bili:{VALID_BVID}:cid:123")


class CoverInfoTests(unittest.TestCase):
    def test_cover_info_returns_video_cover_and_page_first_frame(self):
        result = cover_info_from_video_data(
            {
                "bvid": VALID_BVID,
                "pic": "http://i0.hdslb.com/bfs/archive/main.jpg",
                "owner": {"face": "//i0.hdslb.com/face.jpg"},
                "pages": [
                    {"cid": 1, "page": 1, "part": "P1", "first_frame": "//i0.hdslb.com/p1.jpg"},
                    {"cid": 2, "page": 2, "part": "P2", "first_frame": "http://i1.hdslb.com/p2.jpg"},
                ],
            },
            cid=2,
        )

        self.assertEqual(result["videoCover"], "https://i0.hdslb.com/bfs/archive/main.jpg")
        self.assertEqual(result["cover"], "https://i1.hdslb.com/p2.jpg")
        self.assertEqual(result["pageCover"], "https://i1.hdslb.com/p2.jpg")
        self.assertEqual(result["pages"][0]["firstFrame"], "https://i0.hdslb.com/p1.jpg")


class TrackDetailPanelTests(unittest.TestCase):
    def test_intro_normalizes_description_stats_and_pages(self):
        result = normalize_video_intro(
            {
                "bvid": VALID_BVID,
                "cid": 1,
                "title": "Title",
                "desc": "Line 1\nLine 2",
                "dynamic": "Dynamic",
                "pubdate": 1784541600,
                "owner": {"mid": 7, "name": "UP", "face": "//i0.hdslb.com/face.jpg"},
                "stat": {"view": 100, "reply": 3, "like": 9},
                "pages": [{"cid": 1, "page": 1, "part": "Part", "duration": 62}],
            }
        )

        self.assertEqual(result["description"], "Line 1\nLine 2")
        self.assertEqual(result["owner"]["face"], "https://i0.hdslb.com/face.jpg")
        self.assertEqual(result["stats"]["view"], 100)
        self.assertEqual(result["pages"][0]["title"], "Part")

    def test_subtitle_and_chapter_payloads_are_frontend_ready(self):
        player_data = {
            "need_login_subtitle": False,
            "subtitle": {
                "subtitles": [
                    {
                        "id": 1,
                        "lan": "zh-CN",
                        "lan_doc": "中文",
                        "subtitle_url": "//i0.hdslb.com/bfs/subtitle/subtitle.json",
                    }
                ]
            },
            "view_points": [{"from": 1, "to": 3, "content": "Hook", "imgUrl": "//i0.hdslb.com/c.jpg"}],
        }

        subtitles = normalize_player_subtitles(
            player_data,
            VALID_BVID,
            123,
            lines=normalize_subtitle_lines({"body": [{"from": 1.2, "to": 2.5, "content": "<b>Hi</b>"}]}),
            selected_subtitle_id=1,
        )
        chapters = normalize_player_chapters(player_data, VALID_BVID, 123)

        self.assertEqual(subtitles["subtitles"][0]["url"], "https://i0.hdslb.com/bfs/subtitle/subtitle.json")
        self.assertEqual(subtitles["lines"][0]["text"], "Hi")
        self.assertEqual(chapters["chapters"][0]["title"], "Hook")

    def test_subtitle_parser_rejects_non_subtitle_payloads(self):
        self.assertTrue(is_valid_subtitle_url("//i0.hdslb.com/bfs/subtitle/test.json"))
        self.assertTrue(
            is_valid_subtitle_url(
                "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11425171696002329152446932"
            )
        )
        self.assertFalse(is_valid_subtitle_url("https://api.bilibili.com/x/v2/dm/list.so"))
        self.assertFalse(is_valid_subtitle_url("https://example.com/bfs/subtitle/test.json"))

        self.assertEqual(normalize_subtitle_lines({"body": [{"content": "looks like danmaku"}]}), [])
        self.assertEqual(
            normalize_subtitle_lines(
                {
                    "body": [
                        {"from": 1.0, "to": 3.0, "content": "valid"},
                        {"from": 4.0, "to": 3.0, "content": "bad time"},
                        {"from": 4.0, "to": 6.0, "content": ""},
                    ]
                }
            ),
            [{"from": 1.0, "to": 3.0, "text": "valid"}],
        )

    def test_comments_are_normalized(self):
        result = normalize_reply_comments(
            {
                "data": {
                    "cursor": {"all_count": 10, "is_end": False},
                    "replies": [
                        {
                            "rpid": 99,
                            "member": {"mid": 8, "uname": "User", "avatar": "//i0.hdslb.com/a.jpg"},
                            "content": {"message": "Nice"},
                            "like": 4,
                            "rcount": 2,
                            "ctime": 1784541600,
                        }
                    ],
                }
            },
            VALID_BVID,
            100,
            1,
            20,
        )

        self.assertEqual(result["total"], 10)
        self.assertTrue(result["hasMore"])
        self.assertEqual(result["comments"][0]["author"]["avatar"], "https://i0.hdslb.com/a.jpg")


class FakeBiliClient:
    def __init__(self):
        self.calls = 0

    def get_video_info(self, bvid):
        raise AssertionError("cid should be supplied in this test")

    def get_audio_stream(self, bvid, cid, quality="auto"):
        self.calls += 1
        return AudioStreamInfo(
            url=f"https://example.test/{bvid}/{cid}.m4a",
            backup_urls=[],
            duration=100,
            bitrate=128000,
            sample_rate=44100,
            channels=2,
            quality=quality,
            actual_quality="standard",
            stream_id=30232,
        )


class SequencedBiliClient:
    def __init__(self, audio_infos):
        self.audio_infos = list(audio_infos)
        self.calls = 0
        self._lock = threading.Lock()

    def get_video_info(self, bvid):
        raise AssertionError('cid should be supplied in this test')

    def get_audio_stream(self, bvid, cid, quality='auto'):
        with self._lock:
            index = self.calls
            self.calls += 1
        return self.audio_infos[min(index, len(self.audio_infos) - 1)]


class BlockingBiliClient(FakeBiliClient):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    def get_audio_stream(self, bvid, cid, quality='auto'):
        with self._lock:
            self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise AssertionError('test did not release audio lookup')
        return AudioStreamInfo(
            url=f'https://primary.test/{bvid}/{cid}.m4a',
            backup_urls=[],
            duration=100,
            bitrate=128000,
            sample_rate=44100,
            channels=2,
            quality=quality,
            actual_quality='standard',
            stream_id=30232,
        )


class FakeUpstreamResponse:
    def __init__(self, status_code, chunks=(), headers=None):
        self.status_code = status_code
        self._chunks = list(chunks)
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f'unexpected fake HTTP status: {self.status_code}')


class FakeStreamSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


def make_audio_info(url, backup_urls=None):
    return AudioStreamInfo(
        url=url,
        backup_urls=backup_urls or [],
        duration=100,
        bitrate=128000,
        sample_rate=44100,
        channels=2,
        quality='standard',
        actual_quality='standard',
        stream_id=30232,
    )


class StreamServiceTests(unittest.TestCase):
    def test_proxy_headers_normalize_generic_audio_content_type(self):
        upstream = FakeUpstreamResponse(
            200,
            headers={'Content-Type': 'application/octet-stream'},
        )

        headers = StreamService._proxy_response_headers(upstream)

        self.assertEqual(headers['Content-Type'], 'audio/mp4')
        self.assertEqual(headers['Accept-Ranges'], 'bytes')

    def test_audio_info_cache_uses_bvid_cid_quality_alias(self):
        client = FakeBiliClient()
        service = StreamService(client, cache_ttl_seconds=60)

        first = service.get_audio_info(VALID_BVID, cid=123, quality="standard")
        second = service.get_audio_info(VALID_BVID, cid=123, quality="standard")
        third = service.get_audio_info(VALID_BVID, cid=123, quality="high")

        self.assertEqual(first.url, second.url)
        self.assertEqual(client.calls, 2)
        self.assertEqual(third.quality, "high")

    def test_download_audio_to_file_writes_upstream_bytes(self):
        client = SequencedBiliClient([make_audio_info('https://primary.test/audio')])
        session = FakeStreamSession([FakeUpstreamResponse(200, chunks=[b'abc', b'def'])])
        service = StreamService(client, session=session)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "song.m4a"
            result = service.download_audio_to_file(VALID_BVID, 123, "standard", target)

            self.assertEqual(target.read_bytes(), b"abcdef")
            self.assertEqual(result["bytes"], 6)
            self.assertEqual(result["cid"], 123)



    def test_audio_info_single_flight_coalesces_concurrent_cache_misses(self):
        client = BlockingBiliClient()
        service = StreamService(client, cache_ttl_seconds=60)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(
                    service.get_audio_info,
                    VALID_BVID,
                    123,
                    'standard',
                )
                for _ in range(8)
            ]
            self.assertTrue(client.entered.wait(timeout=1))
            client.release.set()
            results = [future.result(timeout=2) for future in futures]

        self.assertEqual(client.calls, 1)
        self.assertEqual({result.url for result in results}, {results[0].url})

    def test_proxy_stream_uses_backup_url_and_closes_upstreams(self):
        client = SequencedBiliClient(
            [make_audio_info('https://primary.test/audio', ['https://backup.test/audio'])]
        )
        failed = FakeUpstreamResponse(502)
        succeeded = FakeUpstreamResponse(
            206,
            chunks=[b'audio'],
            headers={
                'Content-Type': 'audio/mp4',
                'Content-Range': 'bytes 0-4/5',
            },
        )
        session = FakeStreamSession([failed, succeeded])
        service = StreamService(client, session=session)
        flask_app = Flask(__name__)

        with flask_app.test_request_context(
            '/api/tracks/test/123/stream',
            headers={'Range': 'bytes=0-'},
        ):
            response = service.proxy_stream(VALID_BVID, cid=123, quality='standard')
            self.assertEqual(response.get_data(), b'audio')

        self.assertEqual(response.status_code, 206)
        self.assertEqual([call[0] for call in session.calls], [
            'https://primary.test/audio',
            'https://backup.test/audio',
        ])
        self.assertEqual(session.calls[1][1]['headers']['Range'], 'bytes=0-')
        self.assertTrue(failed.closed)
        self.assertTrue(succeeded.closed)

    def test_proxy_stream_refreshes_expired_playurl_once(self):
        client = SequencedBiliClient(
            [
                make_audio_info('https://expired.test/audio'),
                make_audio_info('https://fresh.test/audio'),
            ]
        )
        expired = FakeUpstreamResponse(403)
        succeeded = FakeUpstreamResponse(200, chunks=[b'fresh'])
        session = FakeStreamSession([expired, succeeded])
        service = StreamService(client, session=session)
        flask_app = Flask(__name__)

        with flask_app.test_request_context('/api/tracks/test/123/stream'):
            response = service.proxy_stream(VALID_BVID, cid=123, quality='standard')
            self.assertEqual(response.get_data(), b'fresh')

        self.assertEqual(client.calls, 2)
        self.assertEqual(
            [call[0] for call in session.calls],
            ['https://expired.test/audio', 'https://fresh.test/audio'],
        )
        self.assertTrue(expired.closed)
        self.assertTrue(succeeded.closed)


class FakeAppStreamService:
    def __init__(self):
        self.last_quality = None

    def get_audio_info(self, bvid, cid=None, quality="auto"):
        self.last_quality = quality
        return AudioStreamInfo(
            url="https://example.test/audio.m4a",
            backup_urls=[],
            duration=100,
            bitrate=128000,
            sample_rate=44100,
            channels=2,
            quality=quality,
            actual_quality="standard",
            stream_id=30232,
        )


class AppEndpointTests(unittest.TestCase):
    def test_http_player_compatibility_is_stateless_and_observable(self):
        import app as app_module

        response = app_module.app.test_client().get(
            '/api/player/status',
            headers={'X-Request-ID': 'test-request-123'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Request-ID'], 'test-request-123')
        self.assertIn('app_headers;dur=', response.headers['Server-Timing'])
        self.assertEqual(
            response.get_json()['data'],
            {'has_video': False, 'video_info': None},
        )
        self.assertFalse(hasattr(app_module, 'socketio'))
        self.assertFalse(hasattr(app_module, 'current_video_info'))

    def test_stream_info_returns_part_level_proxy_url(self):
        import app as app_module

        original_stream_service = app_module.stream_service
        app_module.stream_service = FakeAppStreamService()
        try:
            response = app_module.app.test_client().get(
                f"/api/tracks/{VALID_BVID}/123/stream-info?quality=high",
                base_url="http://127.0.0.1:5000",
            )
        finally:
            app_module.stream_service = original_stream_service

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["cid"], 123)
        self.assertEqual(
            payload["data"]["url"],
            f"http://127.0.0.1:5000/api/tracks/{VALID_BVID}/123/stream?quality=high",
        )

    def test_stream_info_uses_audio_quality_preference_when_quality_is_omitted(self):
        import app as app_module

        fake_stream_service = FakeAppStreamService()

        class FakeSettings:
            def get_audio_quality_preference(self):
                return "standard"

        original_stream_service = app_module.stream_service
        original_settings_service = app_module.settings_service
        app_module.stream_service = fake_stream_service
        app_module.settings_service = FakeSettings()
        try:
            response = app_module.app.test_client().get(
                f"/api/tracks/{VALID_BVID}/123/stream-info",
                base_url="http://127.0.0.1:5000",
            )
        finally:
            app_module.stream_service = original_stream_service
            app_module.settings_service = original_settings_service

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(fake_stream_service.last_quality, "standard")
        self.assertIn("quality=standard", payload["data"]["url"])

    def test_playlist_batch_endpoints_preview_and_write(self):
        import app as app_module

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            test_library = LibraryService(db_path)
            original_library_service = app_module.library_service
            app_module.library_service = test_library
            try:
                client = app_module.app.test_client()
                playlist = client.post(
                    "/api/library/playlists",
                    json={"name": "Batch API"},
                ).get_json()["data"]
                track = Track(bvid=VALID_BVID, cid=123, title="Batch Track").to_dict()

                preview = client.post(
                    f"/api/library/playlists/{playlist['id']}/items:preview",
                    json={"tracks": [track, track], "trackIds": ["missing"]},
                )
                write = client.post(
                    f"/api/library/playlists/{playlist['id']}/items:batch",
                    json={"tracks": [track, track], "trackIds": ["missing"]},
                )
            finally:
                app_module.library_service = original_library_service

        preview_payload = preview.get_json()
        write_payload = write.get_json()
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview_payload["data"]["added"], 1)
        self.assertEqual(preview_payload["data"]["duplicated"], 1)
        self.assertEqual(preview_payload["data"]["unavailable"], 1)
        self.assertEqual(write.status_code, 200)
        self.assertEqual(write_payload["data"]["added"], 1)

    def test_image_proxy_host_allowlist(self):
        import app as app_module

        self.assertTrue(app_module._is_allowed_image_host("i0.hdslb.com"))
        self.assertTrue(app_module._is_allowed_image_host("member.bilibili.com"))
        self.assertFalse(app_module._is_allowed_image_host("example.com"))

    def test_image_proxy_blocks_redirect_to_disallowed_host(self):
        import app as app_module

        redirect = FakeUpstreamResponse(
            302,
            headers={'Location': 'http://127.0.0.1/private'},
        )
        fake_session = FakeStreamSession([redirect])
        original_session = app_module._image_session
        app_module._image_session = fake_session
        try:
            response = app_module.app.test_client().get(
                '/api/images/proxy',
                query_string={'url': 'https://i0.hdslb.com/image.jpg'},
            )
        finally:
            app_module._image_session = original_session

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(fake_session.calls), 1)
        self.assertTrue(redirect.closed)

    def test_image_proxy_closes_upstream_and_reports_timing(self):
        import app as app_module

        upstream = FakeUpstreamResponse(
            200,
            chunks=[b'image'],
            headers={'Content-Type': 'image/jpeg', 'Content-Length': '5'},
        )
        fake_session = FakeStreamSession([upstream])
        original_session = app_module._image_session
        app_module._image_session = fake_session
        try:
            response = app_module.app.test_client().get(
                '/api/images/proxy',
                query_string={'url': 'https://i0.hdslb.com/image.jpg'},
            )
        finally:
            app_module._image_session = original_session

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(), b'image')
        self.assertIn('image_upstream_headers;dur=', response.headers['Server-Timing'])
        self.assertIn('app_headers;dur=', response.headers['Server-Timing'])
        self.assertTrue(upstream.closed)


class ModelTests(unittest.TestCase):
    def test_make_track_id_is_part_level(self):
        self.assertEqual(make_track_id(VALID_BVID, 123), f"bili:{VALID_BVID}:cid:123")


if __name__ == "__main__":
    unittest.main()
