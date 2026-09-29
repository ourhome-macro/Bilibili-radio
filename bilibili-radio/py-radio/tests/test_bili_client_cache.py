import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from bili_client import BiliClient
from constant import BilibiliAPI as APIConst
from error_code import APIError


VALID_BVID = "BV1Q541167Qg"
VALID_AID = 455017605
VALID_CID = 123
IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"
AI_SUBTITLE_URL = (
    f"https://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{VALID_AID}{VALID_CID}sourcehash"
)
FOREIGN_AI_SUBTITLE_URL = (
    f"https://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{VALID_AID + 1}{VALID_CID}sourcehash"
)
MANUAL_SUBTITLE_URL = "https://i0.hdslb.com/bfs/subtitle/manual-subtitle.json"
MIXED_CASE_AI_SUBTITLE_URL = (
    f"https://aisubtitle.hdslb.com/BFS/AI_SUBTITLE/PROD/{VALID_AID}{VALID_CID}sourcehash"
)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, reason="OK"):
        self._payload = payload or {}
        self.status_code = status_code
        self.reason = reason

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            error = requests.HTTPError(f"{self.status_code} {self.reason}")
            error.response = self
            raise error

    def json(self):
        return self._payload


class NonJsonResponse(FakeResponse):
    def json(self):
        raise ValueError("not JSON")


class CountingSession:
    def __init__(self, delay_seconds=0):
        self.delay_seconds = delay_seconds
        self.calls = []
        self.lock = threading.Lock()

    def get(self, url, **kwargs):
        params = kwargs.get("params") or {}
        with self.lock:
            self.calls.append((url, params))
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if url == APIConst.NAV_URL:
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
                        }
                    },
                }
            )
        if url == APIConst.PLAYER_INFO_URL:
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "aid": int(params["aid"]),
                        "bvid": params["bvid"],
                        "cid": int(params["cid"]),
                        "subtitle": {"subtitles": []},
                    },
                }
            )
        return FakeResponse(
            {
                "code": 0,
                "data": {
                    "aid": VALID_AID,
                    "bvid": VALID_BVID,
                    "cid": VALID_CID,
                    "title": "cached",
                    "duration": 100,
                    "pages": [
                        {"cid": VALID_CID, "duration": 100},
                        {"cid": 456, "duration": 100},
                    ],
                },
            }
        )


class WbiSession(CountingSession):
    def __init__(
        self,
        reject_count=0,
        identity_override=None,
        subtitle_lines=None,
        subtitle_url=AI_SUBTITLE_URL,
        extra_subtitle_urls=None,
        nav_code=0,
        reject_http_count=0,
    ):
        super().__init__()
        self.reject_count = reject_count
        self.identity_override = identity_override or {}
        self.subtitle_lines = subtitle_lines
        self.subtitle_url = subtitle_url
        self.extra_subtitle_urls = extra_subtitle_urls or []
        self.nav_code = nav_code
        self.reject_http_count = reject_http_count
        self.nav_calls = 0
        self.player_calls = 0
        self.request_kwargs = []

    def get(self, url, **kwargs):
        params = kwargs.get("params") or {}
        self.request_kwargs.append((url, kwargs))
        with self.lock:
            self.calls.append((url, params))
        if url == APIConst.NAV_URL:
            self.nav_calls += 1
            key_prefix = "a" if self.nav_calls == 1 else "c"
            sub_prefix = "b" if self.nav_calls == 1 else "d"
            return FakeResponse(
                {
                    "code": self.nav_code,
                    "message": "not logged in" if self.nav_code == -101 else "OK",
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{key_prefix * 32}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{sub_prefix * 32}.png",
                        }
                    },
                }
            )
        if url == APIConst.PLAYER_INFO_URL:
            self.player_calls += 1
            if self.player_calls <= self.reject_http_count:
                return NonJsonResponse(status_code=403, reason="Forbidden")
            if self.player_calls <= self.reject_count:
                return FakeResponse({"code": -403, "message": "signature expired"})
            identity = {
                "aid": int(params["aid"]),
                "bvid": params["bvid"],
                "cid": int(params["cid"]),
            }
            identity.update(self.identity_override)
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        **identity,
                        "subtitle": {
                            "subtitles": [
                                {
                                    "id": 9,
                                    "lan": "ai-zh",
                                    "lan_doc": "Chinese",
                                    "subtitle_url": self.subtitle_url,
                                    "type": 1,
                                },
                                *[
                                    {
                                        "id": 10 + index,
                                        "lan": "ai-zh",
                                        "lan_doc": "Chinese",
                                        "subtitle_url": subtitle_url,
                                        "type": 1,
                                    }
                                    for index, subtitle_url in enumerate(self.extra_subtitle_urls)
                                ],
                            ]
                        },
                    },
                }
            )
        if url == APIConst.SPACE_INFO_URL:
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "mid": int(params["mid"]),
                        "name": "Space UP",
                        "face": "//i0.hdslb.com/face.jpg",
                        "sign": "profile sign",
                    },
                }
            )
        if url == APIConst.SPACE_ARCHIVE_URL:
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "list": {
                            "vlist": [
                                {
                                    "bvid": VALID_BVID,
                                    "title": "Space Track",
                                    "pic": "//i0.hdslb.com/cover.jpg",
                                    "length": "03:00",
                                    "play": 100,
                                    "created": 1784541600,
                                }
                            ],
                            "tlist": {},
                        },
                        "page": {"count": 1},
                    },
                }
            )
        if url == self.subtitle_url:
            return FakeResponse({"body": self.subtitle_lines or []})
        return FakeResponse(
            {
                "code": 0,
                "data": {
                    "aid": VALID_AID,
                    "bvid": VALID_BVID,
                    "cid": VALID_CID,
                    "title": "cached",
                    "duration": 100,
                    "pages": [{"cid": VALID_CID, "duration": 100}],
                },
            }
        )


class BiliClientMetadataCacheTests(unittest.TestCase):
    def test_video_detail_cache_returns_isolated_payloads(self):
        client = BiliClient()
        session = CountingSession()
        client.session = session

        first = client._get_video_detail_payload(VALID_BVID)
        first["title"] = "mutated"
        second = client._get_video_detail_payload(VALID_BVID)

        self.assertEqual(second["title"], "cached")
        self.assertEqual(len(session.calls), 1)

    def test_player_info_cache_is_scoped_by_cid(self):
        client = BiliClient()
        session = CountingSession()
        client.session = session

        client._get_player_info_payload(VALID_BVID, 123)
        client._get_player_info_payload(VALID_BVID, 123)
        client._get_player_info_payload(VALID_BVID, 456)

        player_calls = [call for call in session.calls if call[0] == APIConst.PLAYER_INFO_URL]
        self.assertEqual(len(player_calls), 2)
        self.assertTrue(all("/x/player/wbi/v2" in call[0] for call in player_calls))
        self.assertEqual(len([call for call in session.calls if call[0] == APIConst.NAV_URL]), 1)

    def test_wbi_signature_matches_known_vector(self):
        signed = BiliClient._sign_wbi_params(
            {"aid": VALID_AID, "bvid": VALID_BVID, "cid": VALID_CID},
            IMG_KEY,
            SUB_KEY,
            timestamp=1700000000,
        )

        self.assertEqual(signed["wts"], "1700000000")
        self.assertEqual(signed["w_rid"], "f2396bb641ca34a8d2806296dd0bd462")

    def test_signature_rejection_refreshes_wbi_keys_once(self):
        client = BiliClient()
        session = WbiSession(reject_count=1)
        client.session = session

        payload = client._get_player_info_payload(VALID_BVID, VALID_CID)

        self.assertEqual(payload["aid"], VALID_AID)
        self.assertEqual(session.nav_calls, 2)
        self.assertEqual(session.player_calls, 2)
        player_params = [params for url, params in session.calls if url == APIConst.PLAYER_INFO_URL]
        self.assertNotEqual(player_params[0]["w_rid"], player_params[1]["w_rid"])

    def test_guest_accepts_wbi_keys_from_nav_not_logged_in_response(self):
        client = BiliClient()
        session = WbiSession(nav_code=-101)
        client.session = session

        payload = client._get_player_info_payload(VALID_BVID, VALID_CID)

        self.assertEqual(payload["aid"], VALID_AID)
        self.assertEqual(session.nav_calls, 1)
        self.assertEqual(session.player_calls, 1)

    def test_space_profile_and_archives_use_instance_wbi_client(self):
        client = BiliClient()
        session = WbiSession()
        client.session = session

        profile = client.get_user_profile(12345)
        archive = client.list_user_tracks(12345, page=1, page_size=20)

        self.assertEqual(profile["mid"], 12345)
        self.assertEqual(profile["name"], "Space UP")
        self.assertEqual(archive["tracks"][0]["ownerMid"], 12345)
        self.assertIn(APIConst.SPACE_INFO_URL, [call[0] for call in session.calls])
        self.assertIn(APIConst.SPACE_ARCHIVE_URL, [call[0] for call in session.calls])

    def test_signature_rejection_is_not_retried_more_than_once(self):
        client = BiliClient()
        session = WbiSession(reject_count=2)
        client.session = session

        with self.assertRaises(APIError):
            client._get_player_info_payload(VALID_BVID, VALID_CID)

        self.assertEqual(session.nav_calls, 2)
        self.assertEqual(session.player_calls, 2)

    def test_non_json_http_403_refreshes_keys_only_once(self):
        client = BiliClient()
        session = WbiSession(reject_http_count=2)
        client.session = session

        with self.assertRaises(APIError):
            client._get_player_info_payload(VALID_BVID, VALID_CID)

        self.assertEqual(session.nav_calls, 2)
        self.assertEqual(session.player_calls, 2)

    def test_player_info_rejects_identity_mismatch(self):
        client = BiliClient()
        session = WbiSession(identity_override={"cid": VALID_CID + 1})
        client.session = session

        with self.assertRaisesRegex(APIError, "identity mismatch"):
            client._get_player_info_payload(VALID_BVID, VALID_CID)

    def test_ai_subtitle_is_bound_to_source_and_accepts_valid_duration(self):
        client = BiliClient(cookie_provider=lambda: "SESSDATA=test")
        session = WbiSession(
            subtitle_lines=[{"from": 90, "to": 105, "content": "last line"}],
        )
        client.session = session

        result = client.get_track_subtitles(VALID_BVID, VALID_CID)

        self.assertEqual(result["lines"][0]["text"], "last line")
        self.assertEqual(result["subtitles"][0]["sourceAid"], VALID_AID)
        self.assertEqual(result["subtitles"][0]["sourceCid"], VALID_CID)

    def test_ai_subtitle_with_abnormal_duration_is_discarded(self):
        client = BiliClient(cookie_provider=lambda: "SESSDATA=test")
        session = WbiSession(
            subtitle_lines=[{"from": 190, "to": 200, "content": "wrong video"}],
        )
        client.session = session

        result = client.get_track_subtitles(VALID_BVID, VALID_CID)

        self.assertEqual(result["subtitles"], [])
        self.assertEqual(result["lines"], [])
        self.assertIsNone(result["activeSubtitleId"])

    def test_ai_subtitle_without_verified_source_is_not_downloaded(self):
        client = BiliClient(cookie_provider=lambda: "SESSDATA=test")
        session = WbiSession(
            subtitle_lines=[{"from": 1, "to": 2, "content": "must not be fetched"}],
        )
        client.session = session
        client._get_player_info_payload = lambda _bvid, _cid: {
            "aid": VALID_AID,
            "bvid": VALID_BVID,
            "cid": VALID_CID,
            "subtitle": {
                "subtitles": [{"id": 9, "subtitle_url": AI_SUBTITLE_URL, "lan": "ai-zh"}]
            },
        }

        result = client.get_track_subtitles(VALID_BVID, VALID_CID)

        self.assertEqual(result["subtitles"], [])
        self.assertFalse(any(url == AI_SUBTITLE_URL for url, _ in session.calls))

    def test_ai_subtitle_url_must_encode_matching_aid_and_cid(self):
        client = BiliClient(cookie_provider=lambda: "SESSDATA=test")
        session = WbiSession(
            subtitle_url=FOREIGN_AI_SUBTITLE_URL,
            subtitle_lines=[{"from": 1, "to": 2, "content": "foreign source"}],
        )
        client.session = session

        result = client.get_track_subtitles(VALID_BVID, VALID_CID)

        self.assertEqual(result["subtitles"], [])
        self.assertFalse(any(url == FOREIGN_AI_SUBTITLE_URL for url, _ in session.calls))

    def test_mixed_manifest_returns_only_bound_subtitles(self):
        client = BiliClient(cookie_provider=lambda: "SESSDATA=test")
        session = WbiSession(
            subtitle_lines=[{"from": 1, "to": 2, "content": "verified source"}],
            extra_subtitle_urls=[FOREIGN_AI_SUBTITLE_URL],
        )
        client.session = session

        result = client.get_track_subtitles(VALID_BVID, VALID_CID)

        self.assertEqual([item["url"] for item in result["subtitles"]], [AI_SUBTITLE_URL])
        self.assertFalse(any(url == FOREIGN_AI_SUBTITLE_URL for url, _ in session.calls))

    def test_subtitle_download_does_not_forward_bilibili_cookie(self):
        client = BiliClient(cookie_provider=lambda: "SESSDATA=secret; bili_jct=secret")
        session = WbiSession(
            subtitle_lines=[{"from": 1, "to": 2, "content": "public signed subtitle"}],
        )
        client.session = session

        client.get_track_subtitles(VALID_BVID, VALID_CID)

        subtitle_requests = [kwargs for url, kwargs in session.request_kwargs if url == AI_SUBTITLE_URL]
        self.assertEqual(len(subtitle_requests), 1)
        self.assertNotIn("Cookie", subtitle_requests[0]["headers"])

    def test_mixed_case_ai_path_still_requires_and_accepts_source_binding(self):
        client = BiliClient(cookie_provider=lambda: "SESSDATA=test")
        session = WbiSession(
            subtitle_url=MIXED_CASE_AI_SUBTITLE_URL,
            subtitle_lines=[{"from": 1, "to": 2, "content": "verified source"}],
        )
        client.session = session

        result = client.get_track_subtitles(VALID_BVID, VALID_CID)

        self.assertEqual(result["subtitles"][0]["url"], MIXED_CASE_AI_SUBTITLE_URL)
        self.assertEqual(result["lines"][0]["text"], "verified source")

    def test_manual_subtitle_without_verified_source_is_not_downloaded(self):
        client = BiliClient(cookie_provider=lambda: "SESSDATA=test")
        session = WbiSession(
            subtitle_url=MANUAL_SUBTITLE_URL,
            subtitle_lines=[{"from": 1, "to": 2, "content": "must not be fetched"}],
        )
        client.session = session
        client._get_player_info_payload = lambda _bvid, _cid: {
            "aid": VALID_AID,
            "bvid": VALID_BVID,
            "cid": VALID_CID,
            "subtitle": {
                "subtitles": [
                    {"id": 9, "subtitle_url": MANUAL_SUBTITLE_URL, "lan": "zh-CN"}
                ]
            },
        }

        result = client.get_track_subtitles(VALID_BVID, VALID_CID)

        self.assertEqual(result["subtitles"], [])
        self.assertFalse(any(url == MANUAL_SUBTITLE_URL for url, _ in session.calls))

    def test_concurrent_detail_requests_share_one_upstream_call(self):
        client = BiliClient()
        session = CountingSession(delay_seconds=0.05)
        client.session = session

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: client._get_video_detail_payload(VALID_BVID), range(8)))

        self.assertTrue(all(result["cid"] == 123 for result in results))
        self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
