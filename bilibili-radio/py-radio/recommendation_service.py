from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from bili_client import BiliClient
from database import DEFAULT_DB_PATH, LEGACY_OWNER_USER_ID, get_connection, init_db
from library_service import LibraryService, utc_now
from models import Track


RECENT_LISTEN_DAYS = 7
RECENT_RECOMMEND_DAYS = 7
DEFAULT_RECOMMENDATION_LIMIT = 8
MAX_RECOMMENDATION_LIMIT = 8
EXPLORE_SLOT_COUNT = 5
HIGH_SCORE_SLOT_COUNT = MAX_RECOMMENDATION_LIMIT - EXPLORE_SLOT_COUNT
POPULAR_MUSIC_QUERY = "音乐"
TAG_SEARCH_SUFFIX = "音乐"


@dataclass
class UserProfile:
    frequent_owner_mids: set[int] = field(default_factory=set)
    liked_owner_mids: set[int] = field(default_factory=set)
    common_tags: set[str] = field(default_factory=set)
    repeated_owner_mids: set[int] = field(default_factory=set)
    completed_owner_mids: set[int] = field(default_factory=set)
    recently_heard_track_ids: set[str] = field(default_factory=set)
    recently_recommended_track_ids: set[str] = field(default_factory=set)
    skipped_track_ids: set[str] = field(default_factory=set)


@dataclass
class CandidateDraft:
    track: Track
    sources: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)


@dataclass
class RecommendationCandidate:
    track: dict[str, Any]
    score: float
    source: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "track": self.track,
            "score": round(self.score, 2),
            "source": self.source,
            "reason": self.reason,
        }


class RecommendationService:
    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        user_id: str = LEGACY_OWNER_USER_ID,
        bili_client: Optional[Any] = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.user_id = user_id
        init_db(self.db_path)
        self.library = LibraryService(self.db_path, user_id=self.user_id)
        self.bili_client = bili_client if bili_client is not None else BiliClient()

    def list_recommendations(self, scene: str = "home", limit: int = DEFAULT_RECOMMENDATION_LIMIT) -> dict[str, Any]:
        normalized_scene = self._normalize_scene(scene)
        bounded_limit = min(max(int(limit or DEFAULT_RECOMMENDATION_LIMIT), 1), MAX_RECOMMENDATION_LIMIT)
        profile = self._load_user_profile()
        drafts = self._generate_candidates(profile)
        candidates = [self._score_candidate(draft, profile) for draft in drafts.values()]
        candidates.sort(key=lambda item: item.score, reverse=True)

        selected = self._select_epsilon_greedy(candidates, bounded_limit, normalized_scene, profile)
        if not selected:
            selected = candidates[:bounded_limit]

        self._upsert_candidate_tracks(selected)
        self.record_events(
            [
                {
                    "trackId": item.track["trackId"],
                    "event": "shown",
                    "scene": normalized_scene,
                    "source": item.source,
                    "reason": item.reason,
                    "score": item.score,
                }
                for item in selected
            ]
        )
        return {
            "scene": normalized_scene,
            "items": [item.to_dict() for item in selected],
        }

    def record_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        events = self.record_events([payload])
        return events[0] if events else {}

    def record_events(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now()
        normalized = []
        for payload in payloads:
            track_id = str(payload.get("trackId") or payload.get("track_id") or "").strip()
            if not track_id or not self._track_exists(track_id):
                continue
            event = self._normalize_event(payload.get("event"))
            normalized.append(
                {
                    "trackId": track_id,
                    "event": event,
                    "scene": self._normalize_scene(str(payload.get("scene") or "home")),
                    "source": str(payload.get("source") or "")[:64],
                    "reason": str(payload.get("reason") or "")[:240],
                    "score": float(payload.get("score") or 0),
                    "playedSeconds": max(int(payload.get("playedSeconds") or payload.get("played_seconds") or 0), 0),
                    "completed": bool(payload.get("completed")) or event == "completed",
                    "liked": event == "liked",
                    "skipped": bool(payload.get("skipped")) or event in {"skipped", "dismissed", "dislike"},
                    "createdAt": now,
                }
            )

        if normalized:
            with get_connection(self.db_path) as conn:
                conn.executemany(
                    """
                    INSERT INTO recommendation_events (
                        user_id, track_id, event, scene, source, reason, score, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            self.user_id,
                            item["trackId"],
                            item["event"],
                            item["scene"],
                            item["source"],
                            item["reason"],
                            item["score"],
                            item["createdAt"],
                        )
                        for item in normalized
                    ],
                )
                for item in normalized:
                    self._write_history(conn, item)
        return normalized

    def _generate_candidates(self, profile: UserProfile) -> dict[str, CandidateDraft]:
        candidates: dict[str, CandidateDraft] = {}

        for mid in list(profile.frequent_owner_mids)[:5]:
            for track in self._safe_list_user_tracks(mid, order="click", page_size=24):
                self._add_candidate(candidates, track, "frequent_up")

        for mid in list(profile.liked_owner_mids - profile.frequent_owner_mids)[:5]:
            for track in self._safe_list_user_tracks(mid, order="click", page_size=24):
                self._add_candidate(candidates, track, "liked_up")

        for track, tag in self._local_tracks_with_common_tags(profile.common_tags):
            self._add_candidate(candidates, track, "tag_match", tag=tag)

        for tag in list(profile.common_tags)[:5]:
            for track in self._safe_search_tracks(f"{tag} {TAG_SEARCH_SUFFIX}", page_size=16):
                self._add_candidate(candidates, track, "tag_search", tag=tag)

        for track in self._safe_search_tracks(POPULAR_MUSIC_QUERY, page_size=30):
            self._add_candidate(candidates, track, "popular_music")

        if not candidates:
            for track in self._local_fallback_tracks():
                self._add_candidate(candidates, track, "library")

        return candidates

    def _score_candidate(self, draft: CandidateDraft, profile: UserProfile) -> RecommendationCandidate:
        track = draft.track
        score = 0.0
        reason_parts: list[str] = []

        if track.owner_mid and track.owner_mid in profile.frequent_owner_mids:
            score += 3
            reason_parts.append("常听 UP 的其他稿件")

        matched_tags = sorted(draft.tags & profile.common_tags)
        if matched_tags:
            score += 3
            reason_parts.append(f"标签是「{matched_tags[0]}」")

        if track.owner_mid and track.owner_mid in profile.repeated_owner_mids:
            score += 2
            reason_parts.append("最近重复听过类似内容")

        if track.owner_mid and track.owner_mid in profile.completed_owner_mids:
            score += 2
            reason_parts.append("最近完整听完类似内容")

        if track.track_id in profile.recently_heard_track_ids:
            score -= 3

        if track.track_id in profile.recently_recommended_track_ids:
            score -= 4

        if track.track_id in profile.skipped_track_ids:
            score -= 5

        if "tag_search" in draft.sources and matched_tags:
            reason_parts.append(f"按「{matched_tags[0]}」探索")
        elif "popular_music" in draft.sources and score <= 0:
            reason_parts.append("最近热门音乐稿件")

        if not reason_parts:
            reason_parts.append(self._source_reason(draft.sources))

        return RecommendationCandidate(
            track=track.to_dict(),
            score=score,
            source=self._primary_source(draft.sources),
            reason="；".join(reason_parts[:2]),
        )

    def _select_epsilon_greedy(
        self,
        candidates: list[RecommendationCandidate],
        limit: int,
        scene: str,
        profile: UserProfile,
    ) -> list[RecommendationCandidate]:
        if not candidates:
            return []

        explore_count = min(EXPLORE_SLOT_COUNT, max(limit - HIGH_SCORE_SLOT_COUNT, 0))
        high_count = limit - explore_count
        selected_ids: set[str] = set()
        explore_selected: list[RecommendationCandidate] = []
        high_selected: list[RecommendationCandidate] = []

        explore_pool = [
            item for item in candidates
            if self._is_unfamiliar(item, profile)
            and item.source in {"frequent_up", "liked_up", "tag_search", "popular_music"}
        ][:50]

        seed = f"{self.user_id}:{scene}:{datetime.now(timezone.utc).date().isoformat()}"
        rng = random.Random(seed)
        rng.shuffle(explore_pool)

        for item in explore_pool[:explore_count]:
            item.source = "explore"
            if not item.reason.startswith("探索："):
                item.reason = f"探索：{item.reason}"
            explore_selected.append(item)
            selected_ids.add(item.track["trackId"])

        high_pool = [
            item for item in candidates
            if item.track["trackId"] not in selected_ids
            and not self._is_skipped(item, profile)
            and item.track["trackId"] not in profile.recently_recommended_track_ids
        ]
        for item in high_pool[:high_count]:
            high_selected.append(item)
            selected_ids.add(item.track["trackId"])

        if len(high_selected) < high_count:
            for item in candidates:
                track_id = item.track["trackId"]
                if track_id in selected_ids or self._is_skipped(item, profile):
                    continue
                high_selected.append(item)
                selected_ids.add(track_id)
                if len(high_selected) >= high_count:
                    break

        return (high_selected + explore_selected)[:limit]

    @staticmethod
    def _is_unfamiliar(item: RecommendationCandidate, profile: UserProfile) -> bool:
        track_id = item.track["trackId"]
        return (
            track_id not in profile.recently_heard_track_ids
            and track_id not in profile.recently_recommended_track_ids
            and track_id not in profile.skipped_track_ids
        )

    @staticmethod
    def _is_skipped(item: RecommendationCandidate, profile: UserProfile) -> bool:
        return item.track["trackId"] in profile.skipped_track_ids

    def _load_user_profile(self) -> UserProfile:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_LISTEN_DAYS)).isoformat()
        recommend_cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_RECOMMEND_DAYS)).isoformat()
        profile = UserProfile()

        with get_connection(self.db_path) as conn:
            profile.frequent_owner_mids = {
                int(row["owner_mid"])
                for row in conn.execute(
                    """
                    SELECT t.owner_mid, COUNT(*) + COALESCE(SUM(r.play_count), 0) AS weight
                    FROM tracks t
                    LEFT JOIN recent r ON r.user_id = ? AND r.track_id = t.track_id
                    LEFT JOIN playback_recent pr ON pr.user_id = ? AND pr.track_id = t.track_id
                    WHERE t.owner_mid IS NOT NULL
                      AND (r.track_id IS NOT NULL OR pr.track_id IS NOT NULL)
                    GROUP BY t.owner_mid
                    ORDER BY weight DESC
                    LIMIT 10
                    """,
                    (self.user_id, self.user_id),
                ).fetchall()
                if row["owner_mid"]
            }

            profile.liked_owner_mids = {
                int(row["owner_mid"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT t.owner_mid
                    FROM likes l
                    JOIN tracks t ON t.track_id = l.track_id
                    WHERE l.user_id = ? AND t.owner_mid IS NOT NULL
                    LIMIT 20
                    """,
                    (self.user_id,),
                ).fetchall()
                if row["owner_mid"]
            }

            profile.common_tags = {
                str(row["mood"]).strip()
                for row in conn.execute(
                    """
                    SELECT mood, COUNT(*) AS weight
                    FROM track_reviews
                    WHERE user_id = ? AND TRIM(mood) <> ''
                    GROUP BY mood
                    ORDER BY weight DESC, MAX(updated_at) DESC
                    LIMIT 10
                    """,
                    (self.user_id,),
                ).fetchall()
                if str(row["mood"]).strip()
            }

            profile.repeated_owner_mids = {
                int(row["owner_mid"])
                for row in conn.execute(
                    """
                    SELECT t.owner_mid
                    FROM recent r
                    JOIN tracks t ON t.track_id = r.track_id
                    WHERE r.user_id = ? AND r.play_count >= 2 AND t.owner_mid IS NOT NULL
                    GROUP BY t.owner_mid
                    LIMIT 10
                    """,
                    (self.user_id,),
                ).fetchall()
                if row["owner_mid"]
            }

            profile.completed_owner_mids = {
                int(row["owner_mid"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT t.owner_mid
                    FROM tracks t
                    LEFT JOIN recent r ON r.user_id = ? AND r.track_id = t.track_id
                    LEFT JOIN playback_recent pr ON pr.user_id = ? AND pr.track_id = t.track_id
                    WHERE t.owner_mid IS NOT NULL
                      AND (COALESCE(r.completed, 0) = 1 OR COALESCE(pr.completed, 0) = 1)
                    LIMIT 20
                    """,
                    (self.user_id, self.user_id),
                ).fetchall()
                if row["owner_mid"]
            }

            profile.recently_heard_track_ids = {
                str(row["track_id"])
                for row in conn.execute(
                    """
                    SELECT track_id FROM recent
                    WHERE user_id = ? AND last_played_at >= ?
                    UNION
                    SELECT track_id FROM playback_recent
                    WHERE user_id = ? AND last_played_at >= ?
                    """,
                    (self.user_id, cutoff, self.user_id, cutoff),
                ).fetchall()
            }

            profile.recently_recommended_track_ids = {
                str(row["track_id"])
                for row in conn.execute(
                    """
                    SELECT track_id FROM recommendation_history
                    WHERE user_id = ? AND recommended_at >= ?
                    UNION
                    SELECT track_id FROM recommendation_events
                    WHERE user_id = ? AND event = 'shown' AND created_at >= ?
                    """,
                    (self.user_id, recommend_cutoff, self.user_id, recommend_cutoff),
                ).fetchall()
            }

            profile.skipped_track_ids = {
                str(row["track_id"])
                for row in conn.execute(
                    """
                    SELECT track_id FROM playback_recent
                    WHERE user_id = ? AND skipped = 1
                    UNION
                    SELECT track_id FROM recommendation_feedback
                    WHERE user_id = ? AND skipped = 1
                    UNION
                    SELECT track_id FROM recommendation_events
                    WHERE user_id = ? AND event IN ('skipped', 'dismissed', 'dislike')
                    """,
                    (self.user_id, self.user_id, self.user_id),
                ).fetchall()
            }

        return profile

    def _safe_list_user_tracks(self, mid: int, order: str, page_size: int) -> list[Track]:
        try:
            payload = self.bili_client.list_user_tracks(mid, page=1, page_size=page_size, order=order)
        except Exception:
            return []
        tracks = payload.get("tracks") if isinstance(payload, dict) else []
        result = []
        for item in tracks or []:
            try:
                result.append(Track.from_dict(item))
            except Exception:
                continue
        return result

    def _safe_search_tracks(self, keyword: str, page_size: int) -> list[Track]:
        try:
            tracks = self.bili_client.search(keyword, page=1, page_size=page_size)
        except Exception:
            return []
        result = []
        for item in tracks or []:
            try:
                result.append(item if isinstance(item, Track) else Track.from_dict(item))
            except Exception:
                continue
        return result

    def _local_tracks_with_common_tags(self, tags: set[str]) -> list[tuple[Track, str]]:
        if not tags:
            return []
        placeholders = ",".join("?" for _ in tags)
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, tr.mood
                FROM track_reviews tr
                JOIN tracks t ON t.track_id = tr.track_id
                WHERE tr.user_id = ?
                  AND tr.mood IN ({placeholders})
                ORDER BY tr.updated_at DESC
                LIMIT 50
                """,
                (self.user_id, *tags),
            ).fetchall()
        return [(self.library._track_from_row(row), str(row["mood"])) for row in rows]

    def _local_fallback_tracks(self) -> list[Track]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT t.*
                FROM tracks t
                LEFT JOIN recent r ON r.user_id = ? AND r.track_id = t.track_id
                LEFT JOIN likes l ON l.user_id = ? AND l.track_id = t.track_id
                LEFT JOIN track_reviews tr ON tr.user_id = ? AND tr.track_id = t.track_id
                LEFT JOIN playlist_items pi ON pi.user_id = ? AND pi.track_id = t.track_id
                WHERE r.track_id IS NOT NULL
                   OR l.track_id IS NOT NULL
                   OR tr.track_id IS NOT NULL
                   OR pi.track_id IS NOT NULL
                ORDER BY COALESCE(r.last_played_at, l.created_at, tr.updated_at, t.updated_at) DESC
                LIMIT 50
                """,
                (self.user_id, self.user_id, self.user_id, self.user_id),
            ).fetchall()
        return [self.library._track_from_row(row) for row in rows]

    @staticmethod
    def _add_candidate(
        candidates: dict[str, CandidateDraft],
        track: Track,
        source: str,
        tag: Optional[str] = None,
    ) -> None:
        if not track.track_id:
            return
        draft = candidates.get(track.track_id)
        if not draft:
            draft = CandidateDraft(track=track)
            candidates[track.track_id] = draft
        draft.sources.add(source)
        if tag:
            draft.tags.add(tag)

    def _upsert_candidate_tracks(self, candidates: list[RecommendationCandidate]) -> None:
        tracks = []
        for candidate in candidates:
            try:
                tracks.append(Track.from_dict(candidate.track))
            except Exception:
                continue
        if tracks:
            self.library.upsert_tracks(tracks, origin='recommendation')

    def _track_exists(self, track_id: str) -> bool:
        with get_connection(self.db_path) as conn:
            return conn.execute("SELECT 1 FROM tracks WHERE track_id = ?", (track_id,)).fetchone() is not None

    def _write_history(self, conn: Any, item: dict[str, Any]) -> None:
        if item["event"] == "shown":
            conn.execute(
                """
                INSERT INTO recommendation_history (
                    user_id, track_id, recommended_at, clicked, played_seconds,
                    completed, liked, skipped, scene, source, score, reason
                )
                VALUES (?, ?, ?, 0, 0, 0, 0, 0, ?, ?, ?, ?)
                """,
                (
                    self.user_id,
                    item["trackId"],
                    item["createdAt"],
                    item["scene"],
                    item["source"],
                    item["score"],
                    item["reason"],
                ),
            )
            return

        conn.execute("""INSERT INTO recommendation_feedback
            (user_id,track_id,clicked,played_seconds,completed,liked,skipped,updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id,track_id) DO UPDATE SET
                clicked=MAX(recommendation_feedback.clicked,excluded.clicked),
                played_seconds=MAX(recommendation_feedback.played_seconds,excluded.played_seconds),
                completed=MAX(recommendation_feedback.completed,excluded.completed),
                liked=MAX(recommendation_feedback.liked,excluded.liked),
                skipped=MAX(recommendation_feedback.skipped,excluded.skipped),
                updated_at=excluded.updated_at""",
            (self.user_id,item['trackId'],int(item['event'] in {'played','accepted','completed'}),
             item['playedSeconds'],int(item['completed']),int(item['liked']),int(item['skipped']),item['createdAt']))

    @staticmethod
    def _primary_source(sources: set[str]) -> str:
        for source in ["frequent_up", "liked_up", "tag_search", "tag_match", "popular_music", "library"]:
            if source in sources:
                return source
        return next(iter(sources), "library")

    @staticmethod
    def _source_reason(sources: set[str]) -> str:
        source = RecommendationService._primary_source(sources)
        return {
            "frequent_up": "常听 UP 的其他稿件",
            "liked_up": "喜欢歌曲 UP 的其他稿件",
            "tag_search": "同标签搜索结果",
            "tag_match": "标签相同的歌曲",
            "popular_music": "最近热门音乐稿件",
        }.get(source, "来自你的播放和收藏记录")

    @staticmethod
    def _normalize_scene(scene: str) -> str:
        value = (scene or "home").strip().lower()
        return value[:32] or "home"

    @staticmethod
    def _normalize_event(event: Any) -> str:
        value = str(event or "shown").strip().lower()
        allowed = {
            "shown",
            "played",
            "accepted",
            "dismissed",
            "dislike",
            "skipped",
            "completed",
            "liked",
            "unliked",
            "collection_added",
        }
        return value if value in allowed else "shown"
