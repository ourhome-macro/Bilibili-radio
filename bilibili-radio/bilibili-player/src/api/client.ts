import type {
  AdminStatsSummary,
  AdminRoleToggleResult,
  AdminUsersPage,
  AppSettings,
  AppSession,
  AudioStreamInfo,
  AudioQualityPreference,
  AuthQrCode,
  AuthQrStatus,
  AuthStatus,
  BiliUpProfile,
  BiliUpTracksResult,
  FavoriteFolder,
  PlayerQueueSnapshot,
  Playlist,
  LocalDownloadResult,
  RecommendationsResult,
  Track,
  TrackChapters,
  TrackComments,
  TrackCoverInfo,
  TrackIntro,
  TrackReview,
  TrackSubtitles,
} from '@/types'
import type { ProgressEvent, ResumePoint } from '@/audio/playbackProgress'

export function fetchPlaybackResume(trackId: string): Promise<ResumePoint> {
  return apiRequest(`/api/playback/resume/${encodeURIComponent(trackId)}`, {
    signal: AbortSignal.timeout(2500),
  })
}

export function savePlaybackProgress(event: ProgressEvent): Promise<{ accepted: boolean; recentCounted?: boolean }> {
  return apiRequest('/api/playback/events', {
    method: 'POST', body: JSON.stringify(event),
    headers: { 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(2500), keepalive: true,
  })
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''
let csrfToken: string | null = null
let runtimeApiBaseUrl: string | null = null

declare global {
  interface Window {
    __BILIBILI_RADIO_API_BASE_URL__?: string
  }
}

interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: {
    code: string
    message: string
  }
}

export interface SearchTracksResult {
  keyword: string
  page: number
  pageSize: number
  tracks: Track[]
}

export interface TrackDetailResult {
  track: Track
  pages: Track[]
}

export interface TrackStreamInfo extends AudioStreamInfo {
  relativeUrl?: string
  bvid?: string
  cid?: number
  quality?: AudioQualityPreference
  actualQuality?: string
  codec?: string
  fallback?: boolean
}

export interface TrackListResult {
  tracks: Track[]
}

export interface TrackReviewResult {
  review: TrackReview | null
}

export interface PlaylistListResult {
  playlists: Playlist[]
}

export interface PlaylistReplaceResult {
  playlist: Playlist
  total: number
  replaced: number
  duplicated: number
  unavailable: number
}

export interface FavoriteFolderListResult {
  folders: FavoriteFolder[]
}

export interface FavoriteTracksResult {
  mediaId: number
  page: number
  pageSize: number
  hasMore: boolean
  total: number
  unavailable: number
  folder: FavoriteFolder
  tracks: Track[]
}

export interface FavoriteImportResult {
  playlist?: Playlist
  import: {
    total: number
    added: number
    duplicated: number
    unavailable: number
    write: boolean
  }
  favorite: {
    mediaId: number
    folder: FavoriteFolder
    fetched: number
    unavailable: number
    hasMore: boolean
    pagesFetched: number[]
    pageSize: number
    maxPages: number
  }
}

export interface RecommendationEventPayload {
  trackId: string
  event: 'shown' | 'played' | 'accepted' | 'dismissed' | 'dislike' | 'skipped' | 'completed' | 'liked' | 'unliked' | 'collection_added'
  scene?: string
  source?: string
  reason?: string
  score?: number
  playedSeconds?: number
  completed?: boolean
  skipped?: boolean
}

export function setApiCsrfToken(token: string | null | undefined): void {
  csrfToken = token || null
}

export function configureApiBaseUrl(baseUrl: string | null | undefined): void {
  const normalized = normalizeBaseUrl(baseUrl)
  runtimeApiBaseUrl = normalized
  if (normalized) {
    window.__BILIBILI_RADIO_API_BASE_URL__ = normalized
  } else {
    delete window.__BILIBILI_RADIO_API_BASE_URL__
  }
}

export function getApiBaseUrl(): string {
  return normalizeBaseUrl(runtimeApiBaseUrl)
    || normalizeBaseUrl(window.__BILIBILI_RADIO_API_BASE_URL__)
    || normalizeBaseUrl(API_BASE_URL)
}

export function redirectToOidcLogin(next = currentAppLocation()): void {
  const params = new URLSearchParams({ next })
  window.location.assign(apiUrl(`/api/session/login?${params.toString()}`))
}

export function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path
  return `${getApiBaseUrl()}${path}`
}

export function mediaUrl(url?: string | null): string {
  const value = (url || '').trim()
  if (!value) return ''
  if (value.startsWith('data:') || value.startsWith('blob:')) return value
  if (value.startsWith('/') && !value.startsWith('//')) {
    return value.startsWith('/api/') ? apiUrl(value) : value
  }
  if (!/^https?:\/\//.test(value)) return value
  return apiUrl(`/api/images/proxy?url=${encodeURIComponent(value)}`)
}

function normalizeBaseUrl(value: string | null | undefined): string {
  const trimmed = (value || '').trim()
  return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed
}

export class ApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, code = 'UNKNOWN_ERROR', status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

export async function searchTracks(keyword: string, page = 1, pageSize = 20): Promise<SearchTracksResult> {
  const params = new URLSearchParams({
    keyword,
    page: String(page),
    page_size: String(pageSize),
  })

  return apiRequest<SearchTracksResult>(`/api/search?${params.toString()}`)
}

export async function resolveTrackInput(input: string): Promise<TrackDetailResult> {
  const params = new URLSearchParams({ input })
  return apiRequest<TrackDetailResult>(`/api/tracks/resolve?${params.toString()}`)
}

export async function getTrackDetail(bvid: string): Promise<TrackDetailResult> {
  return apiRequest<TrackDetailResult>(`/api/tracks/${encodeURIComponent(bvid)}`)
}

export async function getTrackCoverInfo(bvid: string, cid?: number | null): Promise<TrackCoverInfo> {
  const safeBvid = encodeURIComponent(bvid)
  if (cid != null) {
    return apiRequest<TrackCoverInfo>(`/api/tracks/${safeBvid}/${cid}/cover`)
  }
  return apiRequest<TrackCoverInfo>(`/api/tracks/${safeBvid}/cover`)
}

export async function getTrackIntro(bvid: string, cid?: number | null): Promise<TrackIntro> {
  return apiRequest<TrackIntro>(trackScopedPath(bvid, cid, 'intro'))
}

export async function getTrackSubtitles(bvid: string, cid?: number | null): Promise<TrackSubtitles> {
  return apiRequest<TrackSubtitles>(trackScopedPath(bvid, cid, 'subtitles'))
}

export async function getTrackChapters(bvid: string, cid?: number | null): Promise<TrackChapters> {
  return apiRequest<TrackChapters>(trackScopedPath(bvid, cid, 'chapters'))
}

export async function getTrackComments(
  bvid: string,
  cid?: number | null,
  page = 1,
  pageSize = 20
): Promise<TrackComments> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  return apiRequest<TrackComments>(`${trackScopedPath(bvid, cid, 'comments')}?${params.toString()}`)
}

export async function getTrackStreamInfo(
  bvid: string,
  cid?: number | null,
  quality: AudioQualityPreference = 'auto'
): Promise<TrackStreamInfo> {
  const params = new URLSearchParams({ quality })
  const safeBvid = encodeURIComponent(bvid)
  const path = cid != null
    ? `/api/tracks/${safeBvid}/${cid}/stream-info?${params.toString()}`
    : `/api/tracks/${safeBvid}/stream-info?${params.toString()}`
  const streamInfo = await apiRequest<TrackStreamInfo>(path)
  const relativeUrl = streamInfo.relativeUrl?.trim()

  if (relativeUrl?.startsWith('/') && !relativeUrl.startsWith('//')) {
    return { ...streamInfo, url: apiUrl(relativeUrl) }
  }
  return streamInfo
}

export async function downloadTrackToLocalFile(
  track: Track,
  quality: AudioQualityPreference = 'auto'
): Promise<LocalDownloadResult> {
  return apiRequest<LocalDownloadResult>('/api/downloads/track', {
    method: 'POST',
    body: JSON.stringify({
      bvid: track.bvid,
      cid: track.cid,
      title: track.title,
      quality,
    }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function resetStreamStats(): Promise<void> {
  await apiRequest('/api/stream/stats/reset', { method: 'POST' })
}

export async function fetchPlayerQueue(): Promise<PlayerQueueSnapshot> {
  return apiRequest<PlayerQueueSnapshot>('/api/player/queue')
}

export async function savePlayerQueue(snapshot: PlayerQueueSnapshot): Promise<PlayerQueueSnapshot> {
  return apiRequest<PlayerQueueSnapshot>('/api/player/queue', {
    method: 'PUT',
    body: JSON.stringify({
      queue: snapshot.queue,
      currentIndex: snapshot.currentIndex,
      playMode: snapshot.playMode,
    }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function clearPlayerQueueRemote(): Promise<PlayerQueueSnapshot> {
  return apiRequest<PlayerQueueSnapshot>('/api/player/queue', { method: 'DELETE' })
}

export async function fetchRecent(limit = 100): Promise<Track[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  const data = await apiRequest<TrackListResult>(`/api/library/recent?${params.toString()}`)
  return data.tracks
}

export async function addRecentTrack(
  track: Track,
  playback: { positionMs?: number; listenMs?: number; completed?: boolean } = {}
): Promise<void> {
  await apiRequest('/api/library/recent', {
    method: 'POST',
    body: JSON.stringify({ track, ...playback }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function clearRecentTracks(): Promise<void> {
  await apiRequest('/api/library/recent', { method: 'DELETE' })
}

export async function removeRecentTrack(track: Track): Promise<void> {
  const params = new URLSearchParams()
  if (track.cid != null) params.set('cid', String(track.cid))
  const query = params.toString()
  await apiRequest(`/api/library/recent/${encodeURIComponent(track.bvid)}${query ? `?${query}` : ''}`, {
    method: 'DELETE',
  })
}

export async function fetchLikes(): Promise<Track[]> {
  const data = await apiRequest<TrackListResult>('/api/library/likes')
  return data.tracks
}

export async function addLikeTrack(track: Track): Promise<void> {
  await apiRequest(`/api/library/likes/${encodeURIComponent(track.bvid)}`, {
    method: 'POST',
    body: JSON.stringify({ track }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function removeLikeTrack(track: Track): Promise<void> {
  const params = new URLSearchParams()
  if (track.cid != null) params.set('cid', String(track.cid))
  const query = params.toString()
  await apiRequest(`/api/library/likes/${encodeURIComponent(track.bvid)}${query ? `?${query}` : ''}`, {
    method: 'DELETE',
  })
}

export async function fetchTrackReview(track: Track): Promise<TrackReview | null> {
  const data = await apiRequest<TrackReviewResult>(trackScopedPath(track.bvid, track.cid, 'library-review'))
  return data.review
}

export async function saveTrackReview(
  track: Track,
  rating: number,
  mood: string,
  note = ''
): Promise<TrackReview> {
  return apiRequest<TrackReview>(trackScopedPath(track.bvid, track.cid, 'library-review'), {
    method: 'PUT',
    body: JSON.stringify({ track, rating, mood, note }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function deleteTrackReview(track: Track): Promise<void> {
  await apiRequest(trackScopedPath(track.bvid, track.cid, 'library-review'), { method: 'DELETE' })
}

export async function fetchPlaylists(): Promise<Playlist[]> {
  const data = await apiRequest<PlaylistListResult>('/api/library/playlists')
  return data.playlists
}

export async function createPlaylistRemote(name: string, tracks: Track[] = []): Promise<Playlist> {
  return apiRequest<Playlist>('/api/library/playlists', {
    method: 'POST',
    body: JSON.stringify({ name, tracks }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function createCollectionRemote(
  name: string,
  tracks: Track[] = [],
  sourceType: Playlist['sourceType'] = 'user-created',
  sourceBvid?: string | null,
  cover?: string | null
): Promise<Playlist> {
  return apiRequest<Playlist>('/api/library/playlists', {
    method: 'POST',
    body: JSON.stringify({ name, tracks, sourceType, sourceBvid, cover }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function updatePlaylistRemote(
  id: string,
  payload: { name?: string; cover?: string | null }
): Promise<Playlist> {
  return apiRequest<Playlist>(`/api/library/playlists/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function deletePlaylistRemote(id: string): Promise<void> {
  await apiRequest(`/api/library/playlists/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function addPlaylistItemsRemote(id: string, tracks: Track[]): Promise<void> {
  await apiRequest(`/api/library/playlists/${encodeURIComponent(id)}/items:batch`, {
    method: 'POST',
    body: JSON.stringify({ tracks }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function replacePlaylistItemsRemote(id: string, tracks: Track[]): Promise<PlaylistReplaceResult> {
  return apiRequest<PlaylistReplaceResult>(`/api/library/playlists/${encodeURIComponent(id)}/items`, {
    method: 'PUT',
    body: JSON.stringify({ tracks }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function fetchAuthStatus(refresh = false): Promise<AuthStatus> {
  return refresh
    ? apiRequest<AuthStatus>('/api/auth/status/refresh', { method: 'POST' })
    : apiRequest<AuthStatus>('/api/auth/status')
}

export async function fetchAppSession(): Promise<AppSession> {
  return apiRequest<AppSession>('/api/session/me', undefined, { redirectOnUnauthorized: false })
}

export async function logoutAppSession(): Promise<void> {
  await apiRequest('/api/session/logout', { method: 'POST' })
}

export async function fetchAdminStatsSummary(range = '7d'): Promise<AdminStatsSummary> {
  const params = new URLSearchParams({ range })
  return apiRequest<AdminStatsSummary>(`/api/admin/stats/summary?${params.toString()}`)
}

export async function fetchAdminUsers(page = 1, pageSize = 20): Promise<AdminUsersPage> {
  const params = new URLSearchParams({
    page: String(page),
    pageSize: String(pageSize),
  })
  return apiRequest<AdminUsersPage>(`/api/admin/users?${params.toString()}`)
}

export async function toggleGenshinRole(): Promise<AdminRoleToggleResult> {
  return apiRequest<AdminRoleToggleResult>('/api/admin/genshin', { method: 'POST' })
}

export async function createBiliLoginQr(): Promise<AuthQrCode> {
  return apiRequest<AuthQrCode>('/api/auth/qrcode', { method: 'POST' })
}

export async function pollBiliLoginQr(qrcodeKey: string): Promise<AuthQrStatus> {
  return apiRequest<AuthQrStatus>('/api/auth/qrcode/status', {
    method: 'POST',
    body: JSON.stringify({ qrcodeKey }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function logoutBili(): Promise<void> {
  await apiRequest('/api/auth/logout', { method: 'POST' })
}

export async function fetchBiliFavoriteFolders(upMid?: number): Promise<FavoriteFolder[]> {
  const params = new URLSearchParams()
  if (upMid != null) params.set('upMid', String(upMid))
  const query = params.toString()
  const data = await apiRequest<FavoriteFolderListResult>(`/api/bili/favorites${query ? `?${query}` : ''}`)
  return data.folders
}

export async function fetchBiliFavoriteTracks(
  mediaId: number,
  page = 1,
  pageSize = 20
): Promise<FavoriteTracksResult> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  return apiRequest<FavoriteTracksResult>(`/api/bili/favorites/${mediaId}/tracks?${params.toString()}`)
}

export async function fetchBiliUpProfile(mid: number): Promise<BiliUpProfile> {
  return apiRequest<BiliUpProfile>(`/api/bili/users/${mid}/profile`)
}

export async function fetchBiliUpTracks(
  mid: number,
  page = 1,
  pageSize = 20,
  order: 'pubdate' | 'click' = 'pubdate'
): Promise<BiliUpTracksResult> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    order,
  })
  return apiRequest<BiliUpTracksResult>(`/api/bili/users/${mid}/tracks?${params.toString()}`)
}

export async function fetchSettings(): Promise<AppSettings> {
  return apiRequest<AppSettings>('/api/settings')
}

export async function updateSettings(payload: Partial<AppSettings>): Promise<AppSettings> {
  return apiRequest<AppSettings>('/api/settings', {
    method: 'PATCH',
    body: JSON.stringify(payload),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function importBiliFavoriteToPlaylist(
  playlistId: string,
  mediaId: number,
  maxPages = 10,
  pageSize = 20
): Promise<FavoriteImportResult> {
  return apiRequest<FavoriteImportResult>(`/api/library/playlists/${encodeURIComponent(playlistId)}/import/favorite`, {
    method: 'POST',
    body: JSON.stringify({ mediaId, maxPages, pageSize }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function importBiliFavoriteAsPlaylist(
  mediaId: number,
  name?: string,
  maxPages = 10,
  pageSize = 20
): Promise<FavoriteImportResult> {
  return apiRequest<FavoriteImportResult>('/api/library/playlists/import/favorite', {
    method: 'POST',
    body: JSON.stringify({ mediaId, name, maxPages, pageSize }),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function fetchRecommendations(scene = 'home', limit = 8): Promise<RecommendationsResult> {
  const params = new URLSearchParams({ scene, limit: String(limit) })
  return apiRequest<RecommendationsResult>(`/api/recommendations?${params.toString()}`)
}

export async function recordRecommendationEvent(payload: RecommendationEventPayload): Promise<void> {
  await apiRequest('/api/recommendations/events', {
    method: 'POST',
    body: JSON.stringify(payload),
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function recordAnalysisEvent(event: string, payload: Record<string, unknown> = {}): Promise<void> {
  await apiRequest('/api/analysis/events', {
    method: 'POST',
    body: JSON.stringify({ event, payload }),
    headers: { 'Content-Type': 'application/json' },
  })
}

function trackScopedPath(bvid: string, cid: number | null | undefined, suffix: string): string {
  const safeBvid = encodeURIComponent(bvid)
  if (suffix === 'library-review') {
    return cid != null
      ? `/api/library/reviews/${safeBvid}/${cid}`
      : `/api/library/reviews/${safeBvid}`
  }
  if (cid != null) {
    return `/api/tracks/${safeBvid}/${cid}/${suffix}`
  }
  return `/api/tracks/${safeBvid}/${suffix}`
}

interface ApiRequestOptions {
  redirectOnUnauthorized?: boolean
}

function currentAppLocation(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}` || '/'
}

function isMutation(method?: string): boolean {
  const normalized = (method || 'GET').toUpperCase()
  return !['GET', 'HEAD', 'OPTIONS'].includes(normalized)
}

async function apiRequest<T>(
  path: string,
  init?: RequestInit,
  options: ApiRequestOptions = {}
): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set('Accept', 'application/json')
  if (csrfToken && isMutation(init?.method)) {
    headers.set('X-CSRF-Token', csrfToken)
  }

  const response = await fetch(apiUrl(path), {
    ...init,
    credentials: 'include',
    headers,
  })

  const payload = (await response.json().catch(() => null)) as ApiResponse<T> | null
  if (!response.ok || !payload?.success) {
    const error = payload?.error
    if (response.status === 401 && options.redirectOnUnauthorized !== false) {
      redirectToOidcLogin()
    }
    throw new ApiError(
      error?.message || `Request failed with status ${response.status}`,
      error?.code,
      response.status
    )
  }

  return payload.data as T
}
