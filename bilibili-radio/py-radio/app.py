from __future__ import annotations

import atexit
import os
import re
import secrets
import time
from functools import wraps
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import requests
import click
from flask import Flask, Response, g, has_request_context, request
from flask_cors import CORS
from requests.adapters import HTTPAdapter
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from admin_service import AdminService
from analysis_service import AnalysisService
from auth_service import AuthService
from bili_client import BiliClient
from constant import Server
from database import LEGACY_OWNER_USER_ID, get_connection, init_db
from error_code import APIError, ErrorCode
from identity_service import IdentityService
from library_service import LibraryService
from models import Track, make_track_id, normalize_bvid
from monitoring import (
    record_auth_event,
    record_playback_event as record_playback_metric,
    register_monitoring,
)
from oidc_auth import OIDCAuth
from playback_service import PlaybackService
from queue_service import PlayerQueueService
from recommendation_service import RecommendationService
from result import Result
from settings_service import SettingsService
from stream_service import StreamService


app = Flask(__name__)
app.secret_key = os.getenv('APP_SECRET_KEY') or secrets.token_urlsafe(48)
_secure_cookie_default = os.getenv('AUTH_MODE', 'disabled').strip().lower() == 'oidc'
_secure_cookie_value = os.getenv('SESSION_COOKIE_SECURE')
app.config.update(
    SESSION_COOKIE_NAME='br_oidc_flow',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=(
        _secure_cookie_default
        if _secure_cookie_value is None
        else _secure_cookie_value.strip().lower() in {'1', 'true', 'yes', 'on'}
    ),
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

cors_origins = [
    origin.strip()
    for origin in os.getenv('CORS_ALLOWED_ORIGINS', '').split(',')
    if origin.strip()
]
if _secure_cookie_default and cors_origins:
    allow_http = os.getenv('OIDC_ALLOW_HTTP', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    for origin in cors_origins:
        parsed_origin = urlparse(origin)
        if (
            '*' in origin
            or parsed_origin.scheme not in ({'https', 'http'} if allow_http else {'https'})
            or not parsed_origin.hostname
            or parsed_origin.path not in {'', '/'}
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise RuntimeError('CORS_ALLOWED_ORIGINS must contain exact HTTPS origins')
if cors_origins:
    CORS(
        app,
        origins=cors_origins,
        supports_credentials=True,
        expose_headers=['X-Request-ID', 'Server-Timing'],
    )

DESKTOP_CORS_EXACT_ORIGINS = {
    'http://tauri.localhost',
    'https://tauri.localhost',
    'tauri://localhost',
}
DESKTOP_CORS_LOCAL_ORIGIN_RE = re.compile(r'^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$')


@app.after_request
def apply_desktop_cors(response):
    if os.getenv('APP_RUNTIME', '').strip().lower() != 'desktop':
        return response

    origin = request.headers.get('Origin', '').strip()
    if origin in DESKTOP_CORS_EXACT_ORIGINS or DESKTOP_CORS_LOCAL_ORIGIN_RE.match(origin):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-CSRF-Token'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        response.headers.add('Vary', 'Origin')
    return response

init_db()
identity_service = IdentityService()
oidc_auth = OIDCAuth(app, identity_service)
if oidc_auth.enabled:
    configured_hosts = {
        host.strip()
        for host in os.getenv('APP_TRUSTED_HOSTS', '').split(',')
        if host.strip()
    }
    configured_hosts.update({'127.0.0.1', 'localhost', 'backend'})
    external_hostname = urlparse(oidc_auth.external_url).hostname
    if external_hostname:
        configured_hosts.add(external_hostname)
    app.config['TRUSTED_HOSTS'] = sorted(configured_hosts)
auth_service = AuthService()
library_service = LibraryService()
playback_service = PlaybackService()
queue_service = PlayerQueueService()
recommendation_service = RecommendationService()
settings_service = SettingsService()
analysis_service = AnalysisService()
admin_service = AdminService()


def _request_user_id_or_legacy() -> str:
    if has_request_context():
        user = getattr(g, 'current_user', None)
        if user:
            return str(user['id'])
    return LEGACY_OWNER_USER_ID


def _request_service(attribute: str, legacy_service: Any, factory):
    user_id = _request_user_id_or_legacy()
    if user_id == LEGACY_OWNER_USER_ID or not has_request_context():
        return legacy_service
    service = getattr(g, attribute, None)
    if service is None:
        service = factory(user_id)
        setattr(g, attribute, service)
    return service


def _auth_for_request() -> AuthService:
    return _request_service('_auth_service', auth_service, lambda user_id: AuthService(user_id=user_id))


def _library_for_request() -> LibraryService:
    return _request_service(
        '_library_service', library_service, lambda user_id: LibraryService(user_id=user_id)
    )


def _playback_for_request() -> PlaybackService:
    return _request_service(
        '_playback_service', playback_service, lambda user_id: PlaybackService(user_id=user_id)
    )


def _queue_for_request() -> PlayerQueueService:
    return _request_service(
        '_queue_service', queue_service, lambda user_id: PlayerQueueService(user_id=user_id)
    )


def _recommendations_for_request() -> RecommendationService:
    return _request_service(
        '_recommendation_service',
        recommendation_service,
        lambda user_id: RecommendationService(user_id=user_id, bili_client=bili_client),
    )


def _settings_for_request() -> SettingsService:
    return _request_service(
        '_settings_service', settings_service, lambda user_id: SettingsService(user_id=user_id)
    )


def _analysis_for_request() -> AnalysisService:
    return _request_service(
        '_analysis_service', analysis_service, lambda user_id: AnalysisService(user_id=user_id)
    )


bili_client = BiliClient(cookie_provider=lambda: _auth_for_request().get_cookie_header())
recommendation_service = RecommendationService(bili_client=bili_client)
stream_service = StreamService(bili_client)
register_monitoring(app, user_stats_provider=admin_service.monitoring_user_stats)

_REQUEST_ID_PATTERN = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')
_IMAGE_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_image_session = requests.Session()
_image_adapter = HTTPAdapter(
    pool_connections=8,
    pool_maxsize=32,
    max_retries=0,
    pool_block=True,
)
_image_session.mount('http://', _image_adapter)
_image_session.mount('https://', _image_adapter)


_PUBLIC_API_ENDPOINTS = {'session_me', 'session_login', 'session_callback'}
_UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}


def _close_runtime_clients() -> None:
    for client in (stream_service, _image_session, bili_client):
        try:
            client.close()
        except Exception:
            pass


atexit.register(_close_runtime_clients)


def is_desktop_runtime() -> bool:
    return os.getenv('APP_RUNTIME', '').strip().lower() == 'desktop'


def resolve_bind_host(*, auth_enabled: bool = oidc_auth.enabled) -> str:
    configured_host = os.getenv('APP_BIND_HOST') or os.getenv('APP_DEV_HOST')
    if configured_host:
        return configured_host.strip()
    if is_desktop_runtime():
        return '127.0.0.1'
    return '127.0.0.1' if not auth_enabled else Server.HOST


def resolve_bind_port() -> int:
    configured_port = os.getenv('APP_BIND_PORT') or os.getenv('PORT')
    if not configured_port:
        return Server.PORT
    try:
        port = int(configured_port)
    except ValueError as exc:
        raise RuntimeError('APP_BIND_PORT must be an integer') from exc
    if port < 1 or port > 65535:
        raise RuntimeError('APP_BIND_PORT must be between 1 and 65535')
    return port


def enforce_loopback_binding(host: str, *, auth_enabled: bool = oidc_auth.enabled) -> None:
    if (
        not auth_enabled
        and host not in {'127.0.0.1', '::1', 'localhost'}
        and os.getenv('ALLOW_INSECURE_LOCAL_AUTH', '').strip().lower()
        not in {'1', 'true', 'yes', 'on'}
    ):
        raise RuntimeError(
            'AUTH_MODE=disabled may only bind to loopback; set '
            'ALLOW_INSECURE_LOCAL_AUTH=1 to acknowledge the risk'
        )


@app.teardown_request
def close_request_services(_error=None):
    scoped_auth = getattr(g, '_auth_service', None)
    if scoped_auth is not None:
        try:
            scoped_auth.session.close()
        except Exception:
            pass


@app.before_request
def begin_request():
    g.request_started_at = time.perf_counter()
    supplied_request_id = request.headers.get('X-Request-ID', '')
    g.request_id = (
        supplied_request_id
        if _REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else uuid4().hex
    )
    g.app_session_token = request.cookies.get(oidc_auth.cookie_name)
    g.current_user = oidc_auth.current_user(g.app_session_token)

    if not request.path.startswith('/api/') or request.endpoint in _PUBLIC_API_ENDPOINTS:
        return None
    if request.method == 'OPTIONS':
        return None
    if g.current_user is None:
        raise APIError.auth_required('Application login is required')
    if request.method in _UNSAFE_METHODS and oidc_auth.enabled:
        csrf_token = request.headers.get('X-CSRF-Token')
        if not oidc_auth.validate_csrf(g.app_session_token, csrf_token):
            raise APIError.forbidden('CSRF validation failed')
    return None


@app.after_request
def complete_request(response: Response):
    request_id = getattr(g, 'request_id', uuid4().hex)
    headers_ms = max(
        0.0,
        (time.perf_counter() - getattr(g, 'request_started_at', time.perf_counter()))
        * 1_000,
    )
    response.headers['X-Request-ID'] = request_id
    if request.endpoint in {'session_me', 'session_callback', 'session_logout'}:
        response.headers['Cache-Control'] = 'no-store'
    app_timing = f'app_headers;dur={headers_ms:.1f}'
    existing_timing = response.headers.get('Server-Timing')
    response.headers['Server-Timing'] = (
        f'{existing_timing}, {app_timing}' if existing_timing else app_timing
    )
    app.logger.info(
        'http_request request_id=%s method=%s path=%s status=%s headers_ms=%.1f',
        request_id,
        request.method,
        request.path,
        response.status_code,
        headers_ms,
    )
    return response


@app.errorhandler(APIError)
def handle_api_error(error: APIError):
    return Result.fail(error.message, code=error.code.name).json_with_status(error.status_code)


@app.errorhandler(404)
def handle_not_found(_error):
    return Result.fail("Route not found", code=ErrorCode.NOT_FOUND.name).json_with_status(404)


@app.errorhandler(HTTPException)
def handle_http_exception(error: HTTPException):
    status_code = error.code or 500
    code = ErrorCode.NOT_FOUND.name if status_code == 404 else ErrorCode.VALIDATION_ERROR.name
    return Result.fail(str(error.description), code=code).json_with_status(status_code)


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    app.logger.exception(
        'Unhandled server error request_id=%s: %s',
        getattr(g, 'request_id', '-'),
        error,
    )
    return Result.server_error('Internal server error', code=ErrorCode.UNKNOWN_ERROR.name)


def require_admin(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        user = getattr(g, 'current_user', None)
        if not user or user.get('role') != 'admin':
            raise APIError.forbidden('Administrator access is required')
        return handler(*args, **kwargs)

    return wrapped


@app.get('/health/live')
def health_live():
    return Result.ok({'status': 'ok'}).json()


@app.get('/health/ready')
def health_ready():
    with get_connection() as conn:
        conn.execute('SELECT 1').fetchone()
    return Result.ok({'status': 'ready'}).json()


@app.get('/api/session/me')
def session_me():
    user = getattr(g, 'current_user', None)
    if user is None:
        return Result.ok(
            {
                'authenticated': False,
                'user': None,
                'csrfToken': None,
                'oidcEnabled': oidc_auth.enabled,
                'biliConnected': False,
            }
        ).json()
    bili_status = _auth_for_request().get_status(refresh=False)
    return Result.ok(
        {
            'authenticated': True,
            'user': user,
            'csrfToken': oidc_auth.csrf_token(getattr(g, 'app_session_token', None)),
            'oidcEnabled': oidc_auth.enabled,
            'biliConnected': bool(bili_status.get('isLoggedIn')),
        }
    ).json()


@app.get('/api/session/login')
def session_login():
    return oidc_auth.begin_login(request.args.get('next'))


@app.get('/api/session/callback')
def session_callback():
    try:
        response, _raw_session, _user = oidc_auth.finish_login()
    except Exception:
        record_auth_event('oidc_login', 'error')
        raise
    record_auth_event('oidc_login', 'success')
    return response


@app.post('/api/session/logout')
def session_logout():
    response = Result.ok({'loggedOut': True}).json()
    oidc_auth.logout(response, getattr(g, 'app_session_token', None))
    record_auth_event('oidc_logout', 'success')
    return response


@app.get("/api/search")
def search_tracks():
    keyword = request.args.get("keyword", "")
    page = _int_arg("page", 1)
    page_size = _int_arg("page_size", _int_arg("pageSize", 20))
    tracks = bili_client.search(keyword, page=page, page_size=page_size)
    return Result.ok(
        {
            "keyword": keyword,
            "page": page,
            "pageSize": page_size,
            "tracks": [track.to_dict() for track in tracks],
        }
    ).json()


@app.get("/api/images/proxy")
def proxy_image():
    image_url = request.args.get("url", "")
    return _proxy_image_url(image_url)


@app.get("/api/tracks/<bvid>")
def get_track_detail(bvid: str):
    detail = bili_client.get_video_detail(bvid)
    library_service.upsert_tracks(detail.pages)
    return Result.ok(detail.to_dict()).json()


@app.get("/api/tracks/<bvid>/cover")
def get_track_cover_default(bvid: str):
    cid = request.args.get("cid", type=int)
    return Result.ok(bili_client.get_cover_info(bvid, cid=cid)).json()


@app.get("/api/tracks/<bvid>/<int:cid>/cover")
def get_track_cover_part(bvid: str, cid: int):
    return Result.ok(bili_client.get_cover_info(bvid, cid=cid)).json()


@app.get("/api/tracks/<bvid>/intro")
def get_track_intro_default(bvid: str):
    cid = request.args.get("cid", type=int)
    return Result.ok(bili_client.get_video_intro(bvid, cid=cid)).json()


@app.get("/api/tracks/<bvid>/<int:cid>/intro")
def get_track_intro_part(bvid: str, cid: int):
    return Result.ok(bili_client.get_video_intro(bvid, cid=cid)).json()


@app.get("/api/tracks/<bvid>/subtitles")
def get_track_subtitles_default(bvid: str):
    cid = request.args.get("cid", type=int)
    return Result.ok(bili_client.get_track_subtitles(bvid, cid=cid)).json()


@app.get("/api/tracks/<bvid>/<int:cid>/subtitles")
def get_track_subtitles_part(bvid: str, cid: int):
    return Result.ok(bili_client.get_track_subtitles(bvid, cid=cid)).json()


@app.get("/api/tracks/<bvid>/chapters")
def get_track_chapters_default(bvid: str):
    cid = request.args.get("cid", type=int)
    return Result.ok(bili_client.get_track_chapters(bvid, cid=cid)).json()


@app.get("/api/tracks/<bvid>/<int:cid>/chapters")
def get_track_chapters_part(bvid: str, cid: int):
    return Result.ok(bili_client.get_track_chapters(bvid, cid=cid)).json()


@app.get("/api/tracks/<bvid>/comments")
@app.get("/api/tracks/<bvid>/<int:_cid>/comments")
def get_track_comments(bvid: str, _cid: Optional[int] = None):
    page = _int_arg("page", 1)
    page_size = _int_arg("page_size", _int_arg("pageSize", 20))
    return Result.ok(bili_client.get_track_comments(bvid, page=page, page_size=page_size)).json()


@app.get("/api/tracks/resolve")
def resolve_track_input():
    input_value = request.args.get("input", "")
    bvid = BiliClient.parse_input(input_value)
    if not bvid:
        raise APIError.invalid_input("Cannot parse BVID from input")
    detail = bili_client.get_video_detail(bvid)
    library_service.upsert_tracks(detail.pages)
    return Result.ok(detail.to_dict()).json()


@app.get("/api/tracks/<bvid>/stream-info")
def get_track_stream_info_default(bvid: str):
    cid = request.args.get("cid", type=int)
    quality = request.args.get("quality")
    return Result.ok(_stream_info_payload(bvid, cid=cid, quality=quality)).json()


@app.get("/api/tracks/<bvid>/<int:cid>/stream-info")
def get_track_stream_info_part(bvid: str, cid: int):
    quality = request.args.get("quality")
    return Result.ok(_stream_info_payload(bvid, cid=cid, quality=quality)).json()


@app.get("/api/tracks/<bvid>/stream")
def stream_track_default(bvid: str):
    cid = request.args.get("cid", type=int)
    quality = request.args.get("quality") or _settings_for_request().get_audio_quality_preference()
    return stream_service.proxy_stream(bvid, cid=cid, quality=quality)


@app.get("/api/tracks/<bvid>/<int:cid>/stream")
def stream_track_part(bvid: str, cid: int):
    quality = request.args.get("quality") or _settings_for_request().get_audio_quality_preference()
    return stream_service.proxy_stream(bvid, cid=cid, quality=quality)


@app.post("/api/downloads/track")
def download_track_to_local_file():
    if not is_desktop_runtime():
        raise APIError.validation_error("local download is only available in desktop runtime")
    payload = _json_body()
    bvid = str(payload.get("bvid") or "").strip()
    if not bvid:
        raise APIError.validation_error("bvid is required")
    cid = payload.get("cid")
    resolved_cid = int(cid) if cid not in (None, "") else None
    quality = str(payload.get("quality") or _settings_for_request().get_audio_quality_preference())
    title = str(payload.get("title") or bvid)
    target_path = _unique_download_path(_safe_download_filename(title, "m4a"))
    result = stream_service.download_audio_to_file(
        normalize_bvid(bvid),
        resolved_cid,
        quality,
        target_path,
    )
    return Result.ok(result).json()


@app.get("/api/video/info/<bvid>")
def get_video_info(bvid: str):
    detail = bili_client.get_video_detail(bvid)
    track = detail.info.to_track()
    library_service.upsert_track(track)
    return Result.ok(track.to_dict()).json()


@app.get("/api/video/audio/<bvid>/<int:cid>")
def get_audio_stream(bvid: str, cid: int):
    quality = request.args.get("quality")
    return Result.ok(_stream_info_payload(bvid, cid=cid, quality=quality)).json()


@app.get("/api/player/status")
def get_player_status():
    return Result.ok({'has_video': False, 'video_info': None}).json()


@app.route("/api/player/queue", methods=["GET", "PUT", "DELETE"])
def player_queue():
    if request.method == "GET":
        return Result.ok(_queue_for_request().get_queue()).json()
    if request.method == "DELETE":
        return Result.ok(_queue_for_request().clear_queue()).json()

    payload = _json_body()
    result = _queue_for_request().save_queue(
        _queue_tracks_from_payload(payload),
        current_index=int(payload.get("currentIndex") or payload.get("current_index") or -1),
        play_mode=str(payload.get("playMode") or payload.get("play_mode") or "order"),
    )
    return Result.ok(result).json()


@app.post("/api/player/stop")
def stop_player():
    return Result.ok().json()


@app.get("/api/stream/<bvid>")
def stream_audio_legacy(bvid: str):
    cid = request.args.get('cid', type=int)
    return stream_service.proxy_stream(bvid, cid=cid, quality=request.args.get("quality", "auto"))


@app.get("/api/stream/stats")
@require_admin
def get_stream_stats():
    return Result.ok(stream_service.get_stats()).json()


@app.post("/api/stream/stats/reset")
@require_admin
def reset_stream_stats():
    stream_service.reset_stats()
    return Result.ok().json()


@app.get("/api/library/recent")
def list_recent():
    limit = _int_arg("limit", 100)
    return Result.ok({"tracks": _library_for_request().list_recent(limit=limit)}).json()


@app.delete("/api/library/recent")
def clear_recent():
    return Result.ok(_library_for_request().clear_recent()).json()


@app.delete("/api/library/recent/<bvid>")
def remove_recent(bvid: str):
    cid = request.args.get("cid", type=int)
    return Result.ok(_library_for_request().remove_recent(bvid, cid=cid)).json()


@app.post("/api/library/recent")
def add_recent():
    payload = _json_body()
    track = _resolve_track_from_payload(payload)
    result = _library_for_request().add_recent(
        track,
        position_ms=int(payload.get("positionMs") or payload.get("position_ms") or 0),
        listen_ms=int(payload.get("listenMs") or payload.get("listen_ms") or 0),
        completed=bool(payload.get("completed")),
    )
    return Result.ok(result).json()


@app.get("/api/library/likes")
def list_likes():
    return Result.ok({"tracks": _library_for_request().list_likes()}).json()


@app.post("/api/library/likes/<bvid>")
def add_like(bvid: str):
    payload = _json_body()
    payload.setdefault("bvid", bvid)
    track = _resolve_track_from_payload(payload)
    result = _library_for_request().add_like(track)
    record_playback_metric('favorite')
    return Result.ok(result).json()


@app.delete("/api/library/likes/<bvid>")
def remove_like(bvid: str):
    cid = request.args.get("cid", type=int)
    removed = _library_for_request().remove_like(bvid, cid=cid)
    return Result.ok({"bvid": normalize_bvid(bvid), "cid": cid, "removed": removed}).json()


@app.get("/api/library/reviews/<bvid>")
@app.get("/api/library/reviews/<bvid>/<int:cid>")
def get_track_review(bvid: str, cid: Optional[int] = None):
    cid = cid if cid is not None else request.args.get("cid", type=int)
    return Result.ok({"review": _library_for_request().get_review(bvid, cid=cid)}).json()


@app.put("/api/library/reviews/<bvid>")
@app.put("/api/library/reviews/<bvid>/<int:cid>")
def save_track_review(bvid: str, cid: Optional[int] = None):
    payload = _json_body()
    payload.setdefault("bvid", bvid)
    if cid is not None:
        payload.setdefault("cid", cid)
    track = _resolve_track_from_payload(payload)
    review = _library_for_request().save_review(
        track,
        rating=int(payload.get("rating") or 0),
        mood=str(payload.get("mood") or ""),
        note=str(payload.get("note") or ""),
    )
    _analysis_for_request().record_event(
        {
            "event": "track_reviewed",
            "trackId": review["trackId"],
            "payload": {
                "rating": review["rating"],
                "mood": review["mood"],
                "hasNote": bool(review["note"]),
                "visibility": review["visibility"],
            },
        }
    )
    return Result.ok(review).json()


@app.delete("/api/library/reviews/<bvid>")
@app.delete("/api/library/reviews/<bvid>/<int:cid>")
def delete_track_review(bvid: str, cid: Optional[int] = None):
    cid = cid if cid is not None else request.args.get("cid", type=int)
    return Result.ok(_library_for_request().delete_review(bvid, cid=cid)).json()


@app.route("/api/library/playlists", methods=["GET", "POST"])
def playlists():
    if request.method == "GET":
        return Result.ok({"playlists": _library_for_request().list_playlists()}).json()

    payload = _json_body()
    tracks = _tracks_from_payload(payload)
    playlist = _library_for_request().create_collection(
        payload.get("name", ""),
        tracks=tracks,
        source_type=payload.get("sourceType") or payload.get("source_type") or "user-created",
        source_bvid=payload.get("sourceBvid") or payload.get("source_bvid"),
        cover=payload.get("cover"),
    )
    return Result.ok(playlist).json_with_status(201)


@app.route("/api/library/playlists/<playlist_id>", methods=["GET", "PATCH", "DELETE"])
def playlist_detail(playlist_id: str):
    if request.method == "GET":
        return Result.ok(_library_for_request().get_playlist(playlist_id)).json()
    if request.method == "DELETE":
        return Result.ok(_library_for_request().delete_playlist(playlist_id)).json()

    payload = _json_body()
    playlist = _library_for_request().update_playlist(
        playlist_id,
        name=payload.get("name"),
        cover=payload.get("cover"),
    )
    return Result.ok(playlist).json()


@app.post("/api/library/playlists/<playlist_id>/items:preview")
def preview_playlist_items(playlist_id: str):
    payload = _json_body()
    result = _library_for_request().preview_playlist_items(
        playlist_id,
        tracks=_tracks_from_payload(payload),
        track_ids=_track_ids_from_payload(payload),
    )
    return Result.ok(result).json()


@app.post("/api/library/playlists/<playlist_id>/items:batch")
def batch_playlist_items(playlist_id: str):
    payload = _json_body()
    result = _library_for_request().batch_add_playlist_items(
        playlist_id,
        tracks=_tracks_from_payload(payload),
        track_ids=_track_ids_from_payload(payload),
    )
    return Result.ok(result).json()


@app.put("/api/library/playlists/<playlist_id>/items")
def replace_playlist_items(playlist_id: str):
    payload = _json_body()
    result = _library_for_request().replace_playlist_items(
        playlist_id,
        _tracks_from_payload(payload),
    )
    return Result.ok(result).json()


@app.post("/api/library/playlists/import/favorite")
def import_favorite_to_new_playlist():
    payload = _json_body()
    favorite = _favorite_import_payload(payload)
    tracks = favorite.pop("tracks")
    name = str(payload.get("name") or favorite["folder"].get("title") or "").strip()
    if not name:
        name = f"Bilibili favorite {favorite['mediaId']}"
    user_library = _library_for_request()
    playlist = user_library.create_playlist(name)
    result = user_library.batch_add_playlist_items(playlist["id"], tracks=tracks)
    _analysis_for_request().record_event(
        {
            "event": "favorite_imported",
            "payload": {
                "mediaId": favorite["mediaId"],
                "playlistId": playlist["id"],
                "added": result["added"],
                "duplicated": result["duplicated"],
                "unavailable": result["unavailable"],
            },
        }
    )
    return Result.ok(
        {
            "playlist": user_library.get_playlist(playlist["id"]),
            "import": result,
            "favorite": favorite,
        }
    ).json_with_status(201)


@app.post("/api/library/playlists/<playlist_id>/import/favorite")
def import_favorite_to_playlist(playlist_id: str):
    payload = _json_body()
    favorite = _favorite_import_payload(payload)
    tracks = favorite.pop("tracks")
    result = _library_for_request().batch_add_playlist_items(playlist_id, tracks=tracks)
    _analysis_for_request().record_event(
        {
            "event": "favorite_imported",
            "payload": {
                "mediaId": favorite["mediaId"],
                "playlistId": playlist_id,
                "added": result["added"],
                "duplicated": result["duplicated"],
                "unavailable": result["unavailable"],
            },
        }
    )
    return Result.ok({"import": result, "favorite": favorite}).json()


@app.post("/api/playback/events")
def record_playback_event():
    result = _playback_for_request().record_event(_json_body())
    if result.get('completed'):
        record_playback_event_metric = 'complete'
    elif result.get('skipped'):
        record_playback_event_metric = 'skip'
    elif result.get('event') in {'start', 'play'}:
        record_playback_event_metric = 'play'
    else:
        record_playback_event_metric = None
    if record_playback_event_metric:
        record_playback_metric(record_playback_event_metric)
    return Result.ok(result).json()


@app.get("/api/playback/recent")
def playback_recent():
    limit = _int_arg("limit", 100)
    return Result.ok({"tracks": _playback_for_request().list_recent(limit=limit)}).json()


@app.get("/api/playback/resume/<path:track_id>")
def playback_resume(track_id: str):
    return Result.ok(_playback_for_request().get_resume(track_id)).json()


@app.get("/api/recommendations")
def list_recommendations():
    scene = request.args.get("scene", "home")
    limit = _int_arg("limit", 8)
    return Result.ok(_recommendations_for_request().list_recommendations(scene=scene, limit=limit)).json()


@app.post("/api/recommendations/events")
def record_recommendation_event():
    return Result.ok(_recommendations_for_request().record_event(_json_body())).json_with_status(202)


@app.get("/api/auth/status")
def auth_status():
    return Result.ok(_auth_for_request().get_status(refresh=False)).json()


@app.post("/api/auth/status/refresh")
def refresh_auth_status():
    return Result.ok(_auth_for_request().get_status(refresh=True)).json()


@app.post("/api/auth/qrcode")
def auth_qrcode():
    return Result.ok(_auth_for_request().create_qrcode()).json()


@app.post("/api/auth/qrcode/status")
def auth_qrcode_status():
    payload = _json_body()
    qrcode_key = payload.get("qrcodeKey") or payload.get("qrcode_key") or ""
    result = _auth_for_request().poll_qrcode(qrcode_key)
    if result.get('status') == 'confirmed':
        record_auth_event('bilibili_qr', 'success')
    elif result.get('status') == 'expired':
        record_auth_event('bilibili_qr', 'expired')
    return Result.ok(result).json()


@app.get("/api/auth/profile")
def auth_profile():
    return Result.ok(_auth_for_request().get_profile(refresh=False)).json()


@app.post("/api/auth/profile/refresh")
def refresh_auth_profile():
    return Result.ok(_auth_for_request().get_profile(refresh=True)).json()


@app.post("/api/auth/logout")
def auth_logout():
    return Result.ok(_auth_for_request().logout()).json()


@app.get("/api/bili/favorites")
def list_bili_favorites():
    up_mid = request.args.get("up_mid", type=int) or request.args.get("upMid", type=int)
    folders = bili_client.list_favorite_folders(up_mid=up_mid)
    return Result.ok({"folders": [folder.to_dict() for folder in folders]}).json()


@app.get("/api/bili/favorites/<int:media_id>/tracks")
def list_bili_favorite_tracks(media_id: int):
    page = _int_arg("page", 1)
    page_size = _int_arg("page_size", _int_arg("pageSize", 20))
    return Result.ok(bili_client.list_favorite_tracks(media_id, page=page, page_size=page_size)).json()


@app.get("/api/bili/users/<int:mid>/profile")
def bili_user_profile(mid: int):
    return Result.ok(bili_client.get_user_profile(mid)).json()


@app.get("/api/bili/users/<int:mid>/tracks")
def bili_user_tracks(mid: int):
    page = _int_arg("page", 1)
    page_size = _int_arg("page_size", _int_arg("pageSize", 20))
    order = request.args.get("order", "pubdate")
    return Result.ok(
        bili_client.list_user_tracks(mid, page=page, page_size=page_size, order=order)
    ).json()


@app.post("/api/analysis/events")
def record_analysis_event():
    return Result.ok(_analysis_for_request().record_event(_json_body())).json_with_status(202)


@app.get("/api/settings")
def get_settings():
    return Result.ok(_settings_for_request().to_dict()).json()


@app.patch("/api/settings")
def update_settings():
    payload = _json_body()
    if "audioQualityPreference" in payload or "audio_quality_preference" in payload:
        value = payload.get("audioQualityPreference") or payload.get("audio_quality_preference")
        _settings_for_request().set_audio_quality_preference(value)
    if "playbackSpeed" in payload or "playback_speed" in payload:
        value = payload.get("playbackSpeed") or payload.get("playback_speed")
        _settings_for_request().set_playback_speed(value)
    return Result.ok(_settings_for_request().to_dict()).json()


@app.get("/api/settings/audio-quality")
def get_audio_quality_preference():
    return Result.ok(_settings_for_request().to_dict()).json()


@app.patch("/api/settings/audio-quality")
def update_audio_quality_preference():
    payload = _json_body()
    value = payload.get("audioQualityPreference") or payload.get("audio_quality_preference")
    return Result.ok(
        {"audioQualityPreference": _settings_for_request().set_audio_quality_preference(value)}
    ).json()


@app.get('/api/admin/stats/summary')
@require_admin
def admin_stats_summary():
    return Result.ok(admin_service.summary(request.args.get('range', '7d'))).json()


@app.post('/api/admin/genshin')
def toggle_owner_admin_easter_egg():
    user = getattr(g, 'current_user', None)
    if (
        not user
        or user.get('id') != LEGACY_OWNER_USER_ID
        or (
            oidc_auth.enabled
            and (not user.get('issuer') or not user.get('subject'))
        )
    ):
        raise APIError.forbidden('This easter egg belongs to the local owner')
    updated = admin_service.toggle_owner_admin(
        user['id'],
        actor_user_id=user['id'],
        request_id=getattr(g, 'request_id', None),
    )
    return Result.ok(updated).json()


@app.get('/api/admin/users')
@require_admin
def admin_users():
    return Result.ok(
        admin_service.list_users(
            page=_int_arg('page', 1),
            page_size=_int_arg('pageSize', _int_arg('page_size', 20)),
        )
    ).json()


@app.patch('/api/admin/users/<user_id>/role')
@require_admin
def admin_update_user_role(user_id: str):
    user = admin_service.set_role(
        user_id,
        str(_json_body().get('role') or ''),
        actor_user_id=_request_user_id_or_legacy(),
        request_id=getattr(g, 'request_id', None),
    )
    return Result.ok(user).json()


@app.cli.command('claim-legacy-owner')
@click.option('--issuer', required=True, help='Exact OIDC issuer URL.')
@click.option('--subject', required=True, help='Exact OIDC subject identifier.')
def claim_legacy_owner_command(issuer: str, subject: str):
    user = identity_service.claim_legacy_owner(issuer, subject)
    click.echo(f"Claimed {user['id']} as admin for {user['issuer']} / {user['subject']}")


def _json_body() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _int_arg(name: str, default: int) -> int:
    value = request.args.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bool_arg(name: str, default: bool) -> bool:
    value = request.args.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _favorite_import_payload(payload: dict[str, Any]) -> dict[str, Any]:
    media_id = int(payload.get("mediaId") or payload.get("media_id") or 0)
    if media_id <= 0:
        raise APIError.validation_error("mediaId is required")

    max_pages = min(max(int(payload.get("maxPages") or payload.get("max_pages") or 10), 1), 50)
    page_size = min(max(int(payload.get("pageSize") or payload.get("page_size") or 20), 1), 20)
    favorite = bili_client.list_all_favorite_tracks(
        media_id,
        max_pages=max_pages,
        page_size=page_size,
    )
    tracks = favorite.pop("tracks")
    favorite["tracks"] = tracks
    favorite["fetched"] = len(tracks)
    return favorite


def _resolve_track_from_payload(payload: dict[str, Any]) -> Track:
    candidate = payload.get("track")
    if isinstance(candidate, dict):
        return Track.from_dict(candidate)
    if payload.get("title") and payload.get("bvid"):
        return Track.from_dict(payload)

    bvid = str(payload.get("bvid") or "").strip()
    if not bvid:
        raise APIError.validation_error("track or bvid is required")

    cid = payload.get("cid")
    detail = bili_client.get_video_detail(bvid)
    if cid:
        track_id = make_track_id(bvid, int(cid))
        for track in detail.pages:
            if track.track_id == track_id:
                return track
        raise APIError.not_found(f"Track part not found: {track_id}")
    return detail.info.to_track()


def _tracks_from_payload(payload: dict[str, Any]) -> list[Track]:
    tracks = payload.get("tracks")
    if not isinstance(tracks, list):
        return []

    result = []
    for item in tracks:
        if isinstance(item, dict):
            result.append(Track.from_dict(item))
    return result


def _queue_tracks_from_payload(payload: dict[str, Any]) -> list[Track]:
    queue = payload.get("queue")
    if isinstance(queue, list):
        return [Track.from_dict(item) for item in queue if isinstance(item, dict)]
    return _tracks_from_payload(payload)


def _track_ids_from_payload(payload: dict[str, Any]) -> list[str]:
    track_ids = payload.get("trackIds") or payload.get("track_ids") or []
    if not isinstance(track_ids, list):
        return []
    return [str(track_id) for track_id in track_ids if track_id]


def _stream_info_payload(bvid: str, cid: Optional[int], quality: Optional[str]) -> dict[str, Any]:
    resolved_bvid = normalize_bvid(bvid)
    resolved_cid = cid
    if resolved_cid is None:
        resolved_cid = bili_client.get_video_info(resolved_bvid).cid
    resolved_quality = quality or _settings_for_request().get_audio_quality_preference()
    audio_info = stream_service.get_audio_info(resolved_bvid, cid=resolved_cid, quality=resolved_quality)
    payload = audio_info.to_dict()
    relative_url = f"/api/tracks/{resolved_bvid}/{resolved_cid}/stream?quality={resolved_quality}"
    payload.update(
        {
            "url": _absolute_url(relative_url),
            "relativeUrl": relative_url,
            "bvid": resolved_bvid,
            "cid": resolved_cid,
        }
    )
    return payload


def _absolute_url(path: str) -> str:
    return f"{request.host_url.rstrip('/')}{path}"


def _downloads_dir() -> Path:
    configured = os.getenv("DOWNLOADS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        known_downloads = _windows_known_downloads_dir()
        if known_downloads:
            return known_downloads
    userprofile = os.getenv("USERPROFILE", "").strip()
    if userprofile:
        return Path(userprofile).expanduser() / "Downloads"
    return Path.home() / "Downloads"


def _windows_known_downloads_dir() -> Optional[Path]:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    folder_id_downloads = GUID(
        0x374DE290,
        0x123F,
        0x4565,
        (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B),
    )
    path_ptr = wintypes.LPWSTR()
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        result = shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id_downloads),
            0,
            None,
            ctypes.byref(path_ptr),
        )
        if result != 0 or not path_ptr.value:
            return None
        return Path(path_ptr.value)
    except Exception:
        return None
    finally:
        if path_ptr:
            try:
                ole32.CoTaskMemFree(path_ptr)
            except Exception:
                pass


def _safe_download_filename(title: str, extension: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", (title or "audio").strip())
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    if not safe:
        safe = "audio"
    return f"{safe[:100]}.{extension.lstrip('.') or 'm4a'}"


def _unique_download_path(filename: str) -> Path:
    downloads_dir = _downloads_dir()
    path = downloads_dir / filename
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = downloads_dir / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return downloads_dir / f"{stem}-{int(time.time())}{suffix}"


def _proxy_image_url(image_url: str):
    _validate_image_url(image_url)

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.bilibili.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    request_id = getattr(g, 'request_id', '-')
    request_started_at = getattr(g, 'request_started_at', time.perf_counter())
    upstream_started_at = time.perf_counter()
    upstream = _open_image_upstream(image_url, headers)
    upstream_headers_ms = max(
        0.0,
        (time.perf_counter() - upstream_started_at) * 1_000,
    )

    def generate():
        total_bytes = 0
        first_chunk = True
        try:
            for chunk in upstream.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if first_chunk:
                    first_chunk = False
                    app.logger.info(
                        'image_first_byte request_id=%s duration_ms=%.1f',
                        request_id,
                        max(0.0, (time.perf_counter() - request_started_at) * 1_000),
                    )
                yield chunk
        finally:
            upstream.close()
            app.logger.info(
                'image_closed request_id=%s bytes=%s duration_ms=%.1f',
                request_id,
                total_bytes,
                max(0.0, (time.perf_counter() - request_started_at) * 1_000),
            )

    response_headers = {
        "Content-Type": upstream.headers.get("Content-Type", "image/jpeg"),
        "Cache-Control": "public, max-age=86400",
        'Server-Timing': f'image_upstream_headers;dur={upstream_headers_ms:.1f}',
    }
    for name in ('Content-Length', 'ETag', 'Last-Modified'):
        if upstream.headers.get(name):
            response_headers[name] = upstream.headers[name]
    response = Response(generate(), status=upstream.status_code, headers=response_headers)
    response.call_on_close(upstream.close)
    return response


def _open_image_upstream(image_url: str, headers: dict[str, str]):
    current_url = image_url
    for redirect_count in range(4):
        try:
            upstream = _image_session.get(
                current_url,
                headers=headers,
                stream=True,
                timeout=(3, 15),
                allow_redirects=False,
            )
        except requests.Timeout:
            raise APIError.request_timeout("image proxy")
        except requests.RequestException as exc:
            raise APIError.network_error(type(exc).__name__)

        if upstream.status_code in _IMAGE_REDIRECT_STATUSES:
            location = upstream.headers.get('Location')
            upstream.close()
            if not location:
                raise APIError.api_error('Image upstream redirect has no location')
            if redirect_count >= 3:
                raise APIError.api_error('Image upstream redirected too many times')
            current_url = urljoin(current_url, location)
            _validate_image_url(current_url)
            continue

        try:
            upstream.raise_for_status()
        except requests.Timeout:
            upstream.close()
            raise APIError.request_timeout("image proxy")
        except requests.RequestException as exc:
            upstream.close()
            raise APIError.network_error(type(exc).__name__)
        return upstream

    raise APIError.api_error('Image upstream redirected too many times')


def _validate_image_url(image_url: str) -> None:
    parsed = urlparse(image_url or "")
    if parsed.scheme not in {"http", "https"}:
        raise APIError.validation_error("image url must be http or https")
    if not _is_allowed_image_host(parsed.hostname or ""):
        raise APIError.validation_error("image host is not allowed")
    try:
        port = parsed.port
    except ValueError:
        raise APIError.validation_error("image url port is invalid")
    if port not in {None, 80, 443}:
        raise APIError.validation_error("image url port is not allowed")


def _is_allowed_image_host(hostname: str) -> bool:
    host = hostname.lower()
    return (
        host == "hdslb.com"
        or host.endswith(".hdslb.com")
        or host == "bilibili.com"
        or host.endswith(".bilibili.com")
        or host == "bilivideo.com"
        or host.endswith(".bilivideo.com")
    )


if __name__ == "__main__":
    bind_host = resolve_bind_host()
    bind_port = resolve_bind_port()
    print("=" * 60)
    print("Bilibili Radio backend")
    print("=" * 60)
    print(f"HTTP server: http://{bind_host}:{bind_port}")
    print("=" * 60)
    enforce_loopback_binding(bind_host)
    app.run(
        host=bind_host,
        port=bind_port,
        debug=Server.DEBUG,
        threaded=True,
    )
