from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from database import DEFAULT_DB_PATH, LEGACY_OWNER_USER_ID, get_connection, init_db
from error_code import APIError
from models import Track, make_track_id, normalize_bvid
from retention import touch_tracks, now_ms, PROGRESS_TTL_MS


_TRACK_UPSERT_SQL = """
    INSERT INTO tracks (
        track_id, bvid, cid, title, owner, owner_mid, cover, duration, play_count,
        published_at, page, page_title, source, raw_json, updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(track_id) DO UPDATE SET
        bvid = excluded.bvid,
        cid = excluded.cid,
        title = excluded.title,
        owner = excluded.owner,
        owner_mid = excluded.owner_mid,
        cover = excluded.cover,
        duration = excluded.duration,
        play_count = excluded.play_count,
        published_at = excluded.published_at,
        page = excluded.page,
        page_title = excluded.page_title,
        source = excluded.source,
        raw_json = excluded.raw_json,
        updated_at = excluded.updated_at
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class LibraryService:
    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        user_id: str = LEGACY_OWNER_USER_ID,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.user_id = user_id
        init_db(self.db_path)

    def upsert_track(self, track: Track, raw: Optional[dict[str, Any]] = None, *, origin: str = 'library') -> Track:
        self._validate_track(track)
        now = utc_now()
        with get_connection(self.db_path) as conn:
            conn.execute(_TRACK_UPSERT_SQL, self._track_upsert_values(track, raw, now))
            touch_tracks(conn, [track.track_id], origin)
        return track

    def upsert_tracks(self, tracks: list[Track], *, origin: str = 'library') -> list[Track]:
        if not tracks:
            return []
        for track in tracks:
            self._validate_track(track)
        now = utc_now()
        with get_connection(self.db_path) as conn:
            conn.executemany(
                _TRACK_UPSERT_SQL,
                [self._track_upsert_values(track, None, now) for track in tracks],
            )
            touch_tracks(conn, [track.track_id for track in tracks], origin)
        return tracks

    def get_track(self, track_id: str) -> Optional[Track]:
        with get_connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM tracks WHERE track_id = ?", (track_id,)).fetchone()
        return self._track_from_row(row) if row else None

    def find_tracks_by_bvid(self, bvid: str) -> list[Track]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM tracks WHERE bvid = ? ORDER BY COALESCE(page, 999999), cid",
                (normalize_bvid(bvid),),
            ).fetchall()
        return [self._track_from_row(row) for row in rows]

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT t.*, r.last_played_at, r.play_count AS recent_play_count,
                       CASE WHEN p.last_active_ms > ? THEN p.position_ms
                            WHEN CAST((julianday(r.last_played_at)-2440587.5)*86400000 AS INTEGER) > ?
                            THEN r.position_ms ELSE 0 END AS position_ms,
                       r.listen_ms, COALESCE(p.completed, r.completed) AS completed
                FROM recent r
                JOIN tracks t ON t.track_id = r.track_id
                LEFT JOIN playback_progress p ON p.user_id = r.user_id AND p.track_id = r.track_id
                WHERE r.user_id = ?
                ORDER BY r.last_played_at DESC
                LIMIT ?
                """,
                (now_ms()-PROGRESS_TTL_MS, now_ms()-PROGRESS_TTL_MS, self.user_id, limit),
            ).fetchall()
        return [self._track_payload_with_meta(row) for row in rows]

    def clear_recent(self) -> dict[str, Any]:
        with get_connection(self.db_path) as conn:
            recent = conn.execute("DELETE FROM recent WHERE user_id = ?", (self.user_id,))
            playback_recent = conn.execute(
                "DELETE FROM playback_recent WHERE user_id = ?",
                (self.user_id,),
            )
        return {
            "removed": recent.rowcount,
            "playbackRemoved": playback_recent.rowcount,
        }

    def remove_recent(self, bvid: str, cid: Optional[int] = None) -> dict[str, Any]:
        normalized_bvid = normalize_bvid(bvid)
        with get_connection(self.db_path) as conn:
            if cid is None:
                track_ids = """
                    SELECT track_id
                    FROM tracks
                    WHERE bvid = ?
                """
                recent = conn.execute(
                    f"""
                    DELETE FROM recent
                    WHERE user_id = ?
                      AND track_id IN ({track_ids})
                    """,
                    (self.user_id, normalized_bvid),
                )
                playback_recent = conn.execute(
                    f"""
                    DELETE FROM playback_recent
                    WHERE user_id = ?
                      AND track_id IN ({track_ids})
                    """,
                    (self.user_id, normalized_bvid),
                )
            else:
                track_id = make_track_id(normalized_bvid, cid)
                recent = conn.execute(
                    "DELETE FROM recent WHERE user_id = ? AND track_id = ?",
                    (self.user_id, track_id),
                )
                playback_recent = conn.execute(
                    "DELETE FROM playback_recent WHERE user_id = ? AND track_id = ?",
                    (self.user_id, track_id),
                )
        return {
            "bvid": normalized_bvid,
            "cid": cid,
            "removed": recent.rowcount,
            "playbackRemoved": playback_recent.rowcount,
        }

    def add_recent(
        self,
        track: Track,
        position_ms: int = 0,
        listen_ms: int = 0,
        completed: bool = False,
    ) -> dict[str, Any]:
        self.upsert_track(track)
        now = utc_now()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO recent (
                    user_id, track_id, last_played_at, play_count,
                    position_ms, listen_ms, completed
                )
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(user_id, track_id) DO UPDATE SET
                    last_played_at = excluded.last_played_at,
                    play_count = recent.play_count + 1,
                    position_ms = excluded.position_ms,
                    listen_ms = MAX(recent.listen_ms, excluded.listen_ms),
                    completed = excluded.completed
                """,
                (
                    self.user_id,
                    track.track_id,
                    now,
                    int(position_ms),
                    int(listen_ms),
                    int(completed),
                ),
            )
        return {"track": track.to_dict(), "lastPlayedAt": now}

    def list_likes(self) -> list[dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT t.*, l.created_at
                FROM likes l
                JOIN tracks t ON t.track_id = l.track_id
                WHERE l.user_id = ?
                ORDER BY l.created_at DESC
                """,
                (self.user_id,),
            ).fetchall()
        return [
            {**self._track_from_row(row).to_dict(), "likedAt": row["created_at"]}
            for row in rows
        ]

    def add_like(self, track: Track) -> dict[str, Any]:
        self.upsert_track(track)
        now = utc_now()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO likes (user_id, track_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, track_id) DO NOTHING
                """,
                (self.user_id, track.track_id, now),
            )
        return {"track": track.to_dict(), "likedAt": now}

    def is_liked(self, bvid: str, cid: Optional[int] = None) -> bool:
        with get_connection(self.db_path) as conn:
            if cid is None:
                row = conn.execute(
                    """
                    SELECT 1
                    FROM likes l
                    JOIN tracks t ON t.track_id = l.track_id
                    WHERE l.user_id = ? AND t.bvid = ?
                    LIMIT 1
                    """,
                    (self.user_id, normalize_bvid(bvid)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM likes WHERE user_id = ? AND track_id = ? LIMIT 1",
                    (self.user_id, make_track_id(bvid, cid)),
                ).fetchone()
        return row is not None

    def remove_like(self, bvid: str, cid: Optional[int] = None) -> int:
        with get_connection(self.db_path) as conn:
            if cid is None:
                rows = conn.execute(
                    """
                    DELETE FROM likes
                    WHERE user_id = ?
                      AND track_id IN (SELECT track_id FROM tracks WHERE bvid = ?)
                    """,
                    (self.user_id, normalize_bvid(bvid)),
                )
            else:
                rows = conn.execute(
                    "DELETE FROM likes WHERE user_id = ? AND track_id = ?",
                    (self.user_id, make_track_id(bvid, cid)),
                )
            return rows.rowcount

    def get_review(self, bvid: str, cid: Optional[int] = None) -> Optional[dict[str, Any]]:
        track_id = make_track_id(bvid, cid)
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT tr.*, t.bvid, t.cid, t.title, t.owner, t.cover, t.duration
                FROM track_reviews tr
                JOIN tracks t ON t.track_id = tr.track_id
                WHERE tr.user_id = ? AND tr.track_id = ?
                LIMIT 1
                """,
                (self.user_id, track_id),
            ).fetchone()
        return self._review_payload(row) if row else None

    def save_review(self, track: Track, rating: int, mood: str, note: str = "") -> dict[str, Any]:
        self.upsert_track(track)
        normalized_rating = int(rating)
        if normalized_rating < 1 or normalized_rating > 5:
            raise APIError.validation_error("review rating must be between 1 and 5")
        normalized_mood = (mood or "").strip()
        if not normalized_mood:
            raise APIError.validation_error("review mood is required")
        if len(normalized_mood) > 4:
            raise APIError.validation_error("review label must be at most 4 characters")
        normalized_note = (note or "").strip()
        if len(normalized_note) > 1000:
            raise APIError.validation_error("review note is too long")

        now = utc_now()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO track_reviews (
                    user_id, track_id, rating, mood, note, visibility,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'private', ?, ?)
                ON CONFLICT(user_id, track_id) DO UPDATE SET
                    rating = excluded.rating,
                    mood = excluded.mood,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    self.user_id,
                    track.track_id,
                    normalized_rating,
                    normalized_mood,
                    normalized_note,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT tr.*, t.bvid, t.cid, t.title, t.owner, t.cover, t.duration
                FROM track_reviews tr
                JOIN tracks t ON t.track_id = tr.track_id
                WHERE tr.user_id = ? AND tr.track_id = ?
                """,
                (self.user_id, track.track_id),
            ).fetchone()
        return self._review_payload(row)

    def delete_review(self, bvid: str, cid: Optional[int] = None) -> dict[str, Any]:
        track_id = make_track_id(bvid, cid)
        with get_connection(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM track_reviews WHERE user_id = ? AND track_id = ?",
                (self.user_id, track_id),
            )
        return {"trackId": track_id, "deleted": cursor.rowcount > 0}

    def list_playlists(self) -> list[dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            conn.execute("BEGIN")
            playlists = conn.execute(
                "SELECT * FROM playlists WHERE user_id = ? ORDER BY created_at DESC",
                (self.user_id,),
            ).fetchall()
            item_rows = conn.execute(
                """
                SELECT pi.playlist_id, t.*, pi.position, pi.added_at
                FROM playlist_items pi
                JOIN tracks t ON t.track_id = pi.track_id
                WHERE pi.user_id = ?
                ORDER BY pi.playlist_id, pi.position ASC, pi.added_at ASC
                """,
                (self.user_id,),
            ).fetchall()

        items_by_playlist: dict[str, list[Any]] = {}
        for row in item_rows:
            items_by_playlist.setdefault(row["playlist_id"], []).append(row)
        return [
            self._playlist_payload(
                playlist,
                items_by_playlist.get(playlist["id"], []),
            )
            for playlist in playlists
        ]

    def get_playlist(self, playlist_id: str) -> dict[str, Any]:
        with get_connection(self.db_path) as conn:
            conn.execute("BEGIN")
            playlist = conn.execute(
                "SELECT * FROM playlists WHERE user_id = ? AND id = ?",
                (self.user_id, playlist_id),
            ).fetchone()
            if not playlist:
                raise APIError.not_found(f"Playlist not found: {playlist_id}")
            item_rows = conn.execute(
                """
                SELECT t.*, pi.position, pi.added_at
                FROM playlist_items pi
                JOIN tracks t ON t.track_id = pi.track_id
                WHERE pi.user_id = ? AND pi.playlist_id = ?
                ORDER BY pi.position ASC, pi.added_at ASC
                """,
                (self.user_id, playlist_id),
            ).fetchall()
        return self._playlist_payload(playlist, item_rows)

    def create_playlist(self, name: str, tracks: Optional[list[Track]] = None) -> dict[str, Any]:
        return self.create_collection(name, tracks=tracks)

    def create_collection(
        self,
        name: str,
        tracks: Optional[list[Track]] = None,
        source_type: str = "user-created",
        source_bvid: Optional[str] = None,
        cover: Optional[str] = None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise APIError.validation_error("playlist name is required")
        source_type = self._normalize_source_type(source_type)

        playlist_id = f"pl_{uuid.uuid4().hex[:12]}"
        now = utc_now()
        resolved_cover = cover if cover is not None else (tracks[0].cover if tracks else None)
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO playlists (
                    user_id, id, name, cover, source_type, source_bvid,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.user_id,
                    playlist_id,
                    name,
                    resolved_cover,
                    source_type,
                    source_bvid,
                    now,
                    now,
                ),
            )
        if tracks:
            self.batch_add_playlist_items(playlist_id, tracks=tracks)
        return self.get_playlist(playlist_id)

    def update_playlist(
        self,
        playlist_id: str,
        name: Optional[str] = None,
        cover: Optional[str] = None,
    ) -> dict[str, Any]:
        current = self.get_playlist(playlist_id)
        next_name = (name if name is not None else current["name"]).strip()
        if not next_name:
            raise APIError.validation_error("playlist name is required")
        next_cover = cover if cover is not None else current["cover"]
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE playlists
                SET name = ?, cover = ?, updated_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (next_name, next_cover, utc_now(), self.user_id, playlist_id),
            )
        return self.get_playlist(playlist_id)

    def delete_playlist(self, playlist_id: str) -> dict[str, Any]:
        with get_connection(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM playlists WHERE user_id = ? AND id = ?",
                (self.user_id, playlist_id),
            )
        if cursor.rowcount == 0:
            raise APIError.not_found(f"Playlist not found: {playlist_id}")
        return {"id": playlist_id, "deleted": True}

    def preview_playlist_items(
        self,
        playlist_id: str,
        tracks: Optional[list[Track]] = None,
        track_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return self._batch_playlist_items(playlist_id, tracks or [], track_ids or [], write=False)

    def batch_add_playlist_items(
        self,
        playlist_id: str,
        tracks: Optional[list[Track]] = None,
        track_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return self._batch_playlist_items(playlist_id, tracks or [], track_ids or [], write=True)

    def replace_playlist_items(self, playlist_id: str, tracks: list[Track]) -> dict[str, Any]:
        normalized: list[Track] = []
        seen: set[str] = set()
        unavailable = 0
        duplicated = 0
        for track in tracks:
            if not track.track_id or not track.bvid or not track.title:
                unavailable += 1
                continue
            if track.track_id in seen:
                duplicated += 1
                continue
            seen.add(track.track_id)
            normalized.append(track)

        now = utc_now()
        with get_connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            playlist = conn.execute(
                "SELECT 1 FROM playlists WHERE user_id = ? AND id = ?",
                (self.user_id, playlist_id),
            ).fetchone()
            if not playlist:
                raise APIError.not_found(f"Playlist not found: {playlist_id}")
            conn.executemany(
                _TRACK_UPSERT_SQL,
                [self._track_upsert_values(track, None, now) for track in normalized],
            )
            touch_tracks(conn, [track.track_id for track in normalized], 'playlist')
            conn.execute(
                "DELETE FROM playlist_items WHERE user_id = ? AND playlist_id = ?",
                (self.user_id, playlist_id),
            )
            conn.executemany(
                """
                INSERT INTO playlist_items (
                    user_id, playlist_id, track_id, position, added_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (self.user_id, playlist_id, track.track_id, position, now)
                    for position, track in enumerate(normalized)
                ],
            )
            conn.execute(
                """
                UPDATE playlists
                SET cover = ?, updated_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (
                    normalized[0].cover if normalized else None,
                    now,
                    self.user_id,
                    playlist_id,
                ),
            )

        return {
            "playlist": self.get_playlist(playlist_id),
            "total": len(tracks),
            "replaced": len(normalized),
            "duplicated": duplicated,
            "unavailable": unavailable,
        }

    def _batch_playlist_items(
        self,
        playlist_id: str,
        tracks: list[Track],
        track_ids: list[str],
        write: bool,
    ) -> dict[str, Any]:
        normalized: list[Track] = []
        unavailable = 0

        for track in tracks:
            if not track.track_id or not track.bvid or not track.title:
                unavailable += 1
                continue
            normalized.append(track)

        with get_connection(self.db_path) as conn:
            if write:
                # Reserve the writer before calculating positions so concurrent
                # batches cannot assign the same playlist position.
                conn.execute("BEGIN IMMEDIATE")
            playlist = conn.execute(
                "SELECT 1 FROM playlists WHERE user_id = ? AND id = ?",
                (self.user_id, playlist_id),
            ).fetchone()
            if not playlist:
                raise APIError.not_found(f"Playlist not found: {playlist_id}")

            requested_track_ids = [str(track_id) for track_id in track_ids]
            tracks_by_id: dict[str, Track] = {}
            for offset in range(0, len(requested_track_ids), 500):
                chunk = requested_track_ids[offset : offset + 500]
                if not chunk:
                    continue
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT * FROM tracks WHERE track_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                tracks_by_id.update(
                    (row["track_id"], self._track_from_row(row)) for row in rows
                )

            for track_id in requested_track_ids:
                track = tracks_by_id.get(track_id)
                if track:
                    normalized.append(track)
                else:
                    unavailable += 1

            seen: set[str] = set()
            unique_tracks: list[Track] = []
            input_duplicates = 0
            for track in normalized:
                if track.track_id in seen:
                    input_duplicates += 1
                    continue
                seen.add(track.track_id)
                unique_tracks.append(track)

            existing = {
                row["track_id"]
                for row in conn.execute(
                    "SELECT track_id FROM playlist_items WHERE user_id = ? AND playlist_id = ?",
                    (self.user_id, playlist_id),
                ).fetchall()
            }
            next_position = int(
                conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 AS next_position "
                    "FROM playlist_items WHERE user_id = ? AND playlist_id = ?",
                    (self.user_id, playlist_id),
                ).fetchone()["next_position"]
            )

            to_add = [track for track in unique_tracks if track.track_id not in existing]
            if write:
                now = utc_now()
                conn.executemany(
                    _TRACK_UPSERT_SQL,
                    [self._track_upsert_values(track, None, now) for track in to_add],
                )
                touch_tracks(conn, [track.track_id for track in to_add], 'playlist')
                conn.executemany(
                    """
                    INSERT INTO playlist_items (
                        user_id, playlist_id, track_id, position, added_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            self.user_id,
                            playlist_id,
                            track.track_id,
                            next_position + offset,
                            now,
                        )
                        for offset, track in enumerate(to_add)
                    ],
                )
                if to_add:
                    first_cover = to_add[0].cover
                    conn.execute(
                        """
                        UPDATE playlists
                        SET cover = COALESCE(cover, ?), updated_at = ?
                        WHERE user_id = ? AND id = ?
                        """,
                        (first_cover, now, self.user_id, playlist_id),
                    )

        duplicated = input_duplicates + len(unique_tracks) - len(to_add)
        return {
            "total": len(tracks) + len(track_ids),
            "added": len(to_add),
            "duplicated": duplicated,
            "unavailable": unavailable,
            "write": write,
        }

    def _playlist_payload(
        self,
        playlist: Any,
        item_rows: list[Any],
    ) -> dict[str, Any]:
        tracks = [
            {**self._track_from_row(row).to_dict(), "addedAt": row["added_at"]}
            for row in item_rows
        ]
        return {
            "id": playlist["id"],
            "name": playlist["name"],
            "cover": playlist["cover"],
            "sourceType": playlist["source_type"] if "source_type" in playlist.keys() else "user-created",
            "sourceBvid": playlist["source_bvid"] if "source_bvid" in playlist.keys() else None,
            "tracks": tracks,
            "createdAt": playlist["created_at"],
            "updatedAt": playlist["updated_at"],
        }

    @staticmethod
    def _validate_track(track: Track) -> None:
        if not track.track_id or not track.bvid or not track.title:
            raise APIError.validation_error("track bvid and title are required")

    @staticmethod
    def _track_upsert_values(
        track: Track,
        raw: Optional[dict[str, Any]],
        now: str,
    ) -> tuple[Any, ...]:
        return (
            track.track_id,
            track.bvid,
            track.cid,
            track.title,
            track.owner,
            track.owner_mid,
            track.cover,
            track.duration,
            track.play_count,
            track.published_at,
            track.page,
            track.page_title,
            track.source,
            json.dumps(raw or track.to_dict(), ensure_ascii=False),
            now,
        )

    @staticmethod
    def _track_from_row(row: Any) -> Track:
        return Track(
            track_id=row["track_id"],
            bvid=row["bvid"],
            cid=row["cid"],
            title=row["title"],
            owner=row["owner"],
            owner_mid=row["owner_mid"] if "owner_mid" in row.keys() else None,
            cover=row["cover"],
            duration=row["duration"],
            play_count=row["play_count"],
            published_at=row["published_at"],
            page=row["page"],
            page_title=row["page_title"],
            source=row["source"],
        )

    def _track_payload_with_meta(self, row: Any) -> dict[str, Any]:
        payload = self._track_from_row(row).to_dict()
        payload.update(
            {
                "lastPlayedAt": row["last_played_at"],
                "recentPlayCount": row["recent_play_count"],
                "positionMs": row["position_ms"],
                "listenMs": row["listen_ms"],
                "completed": bool(row["completed"]),
            }
        )
        return payload

    def _review_payload(self, row: Any) -> dict[str, Any]:
        return {
            "trackId": row["track_id"],
            "bvid": row["bvid"],
            "cid": row["cid"],
            "rating": int(row["rating"]),
            "mood": row["mood"],
            "note": row["note"],
            "visibility": row["visibility"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _normalize_source_type(source_type: str) -> str:
        normalized = (source_type or "user-created").strip()
        allowed = {"user-created", "bilibili-multipage", "bilibili-favorite"}
        if normalized not in allowed:
            raise APIError.validation_error("invalid playlist sourceType")
        return normalized
