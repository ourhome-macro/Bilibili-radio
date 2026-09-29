import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import {
  addLikeTrack,
  addPlaylistItemsRemote,
  addRecentTrack,
  createCollectionRemote,
  clearRecentTracks,
  createPlaylistRemote,
  deletePlaylistRemote,
  fetchLikes,
  fetchPlaylists,
  fetchRecent,
  replacePlaylistItemsRemote,
  removeLikeTrack,
  removeRecentTrack,
  recordRecommendationEvent,
  updatePlaylistRemote,
} from '@/api/client'
import type { Playlist, Track } from '@/types'

const RECENT_KEY = 'bili-radio:recent'
const LIKES_KEY = 'bili-radio:likes'
const PLAYLISTS_KEY = 'bili-radio:playlists'

const RECENT_LIMIT = 100
const MIGRATION_CONCURRENCY = 4

interface MigrationResult {
  recent: boolean
  likes: boolean
  playlists: boolean
}

function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

async function runWithConcurrency(tasks: Array<() => Promise<unknown>>, limit: number): Promise<void> {
  if (tasks.length === 0) return

  let cursor = 0
  const errors: unknown[] = []
  const workerCount = Math.min(Math.max(1, limit), tasks.length)

  async function worker() {
    while (cursor < tasks.length) {
      const taskIndex = cursor++
      try {
        await tasks[taskIndex]()
      } catch (error) {
        errors.push(error)
      }
    }
  }

  await Promise.all(Array.from({ length: workerCount }, () => worker()))
  if (errors.length > 0) throw errors[0]
}

export const useLibraryStore = defineStore('library', () => {
  const recent = ref<Track[]>(load(RECENT_KEY, []))
  const likes = ref<Track[]>(load(LIKES_KEY, []))
  const playlists = ref<Playlist[]>(load(PLAYLISTS_KEY, []))
  const isSyncing = ref(false)
  const backendAvailable = ref(false)
  const syncError = ref<string | null>(null)

  let initialized = false
  let initializePromise: Promise<void> | null = null

  watch(recent, (v) => localStorage.setItem(RECENT_KEY, JSON.stringify(v)), { deep: true })
  watch(likes, (v) => localStorage.setItem(LIKES_KEY, JSON.stringify(v)), { deep: true })
  watch(playlists, (v) => localStorage.setItem(PLAYLISTS_KEY, JSON.stringify(v)), { deep: true })

  function initialize(): Promise<void> {
    if (initialized) return Promise.resolve()
    if (initializePromise) return initializePromise

    initializePromise = initializeLibrary().finally(() => {
      initializePromise = null
    })
    return initializePromise
  }

  async function initializeLibrary() {

    const localRecent = [...recent.value]
    const localLikes = [...likes.value]
    const localPlaylists = playlists.value.map((playlist) => ({ ...playlist, tracks: [...playlist.tracks] }))

    isSyncing.value = true
    syncError.value = null
    try {
      const [remoteRecent, remoteLikes, remotePlaylists] = await Promise.all([
        fetchRecent(RECENT_LIMIT),
        fetchLikes(),
        fetchPlaylists(),
      ])

      backendAvailable.value = true
      recent.value = mergeTracks(remoteRecent, localRecent).slice(0, RECENT_LIMIT)
      likes.value = mergeTracks(remoteLikes, localLikes)
      playlists.value = mergePlaylists(remotePlaylists, localPlaylists)

      const migrated = await migrateLocalFallback(
        localRecent,
        localLikes,
        localPlaylists,
        remoteRecent,
        remoteLikes,
        remotePlaylists
      )

      const [finalRecent, finalLikes, finalPlaylists] = await Promise.all([
        migrated.recent ? fetchRecent(RECENT_LIMIT) : Promise.resolve(null),
        migrated.likes ? fetchLikes() : Promise.resolve(null),
        migrated.playlists ? fetchPlaylists() : Promise.resolve(null),
      ])
      if (finalRecent !== null) {
        recent.value = finalRecent.length ? finalRecent : recent.value
      }
      if (finalLikes !== null) {
        likes.value = finalLikes.length ? finalLikes : likes.value
      }
      if (finalPlaylists !== null) {
        playlists.value = finalPlaylists.length ? finalPlaylists : playlists.value
      }
      initialized = true
    } catch (error) {
      backendAvailable.value = false
      syncError.value = error instanceof Error ? error.message : '本地库同步失败'
      initialized = true
    } finally {
      isSyncing.value = false
    }
  }

  async function refreshFromBackend() {
    isSyncing.value = true
    syncError.value = null
    try {
      const [remoteRecent, remoteLikes, remotePlaylists] = await Promise.all([
        fetchRecent(RECENT_LIMIT),
        fetchLikes(),
        fetchPlaylists(),
      ])
      backendAvailable.value = true
      recent.value = remoteRecent
      likes.value = remoteLikes
      playlists.value = remotePlaylists
    } catch (error) {
      backendAvailable.value = false
      syncError.value = error instanceof Error ? error.message : '本地库刷新失败'
      throw error
    } finally {
      isSyncing.value = false
    }
  }

  function addRecent(
    track: Track,
    playback: { positionMs?: number; listenMs?: number; completed?: boolean } = {}
  ) {
    const current = recent.value.find((t) => isSameTrack(t, track))
    const nextTrack: Track = {
      ...track,
      recentPlayCount: (current?.recentPlayCount ?? 0) + 1,
      positionMs: playback.positionMs ?? current?.positionMs ?? 0,
      listenMs: Math.max(playback.listenMs ?? 0, current?.listenMs ?? 0),
      completed: playback.completed ?? current?.completed ?? false,
    }
    recent.value = [nextTrack, ...recent.value.filter((t) => !isSameTrack(t, track))].slice(0, RECENT_LIMIT)
    if (backendAvailable.value) {
      void addRecentTrack(track, playback).catch(handleBackgroundError)
    }
  }

  async function refreshRecent() {
    recent.value = await fetchRecent(RECENT_LIMIT)
  }

  function updateRecentProgress(track: Track, positionMs: number, completed: boolean) {
    const current = recent.value.find(item => isSameTrack(item, track))
    if (current) {
      current.positionMs = positionMs
      current.completed = completed
    }
  }

  function clearRecent() {
    recent.value = []
    if (backendAvailable.value) {
      void clearRecentTracks().catch(handleBackgroundError)
    }
  }

  function removeRecent(track: Track) {
    recent.value = recent.value.filter((item) => !isSameTrack(item, track))
    if (backendAvailable.value) {
      void removeRecentTrack(track).catch(handleBackgroundError)
    }
  }

  function isLiked(bvid: string): boolean {
    return likes.value.some((t) => t.bvid === bvid)
  }

  function isTrackLiked(track: Track): boolean {
    return likes.value.some((t) => isSameTrack(t, track))
  }

  function toggleLike(track: Track) {
    if (isTrackLiked(track)) {
      likes.value = likes.value.filter((t) => !isSameTrack(t, track))
      recordBehavior(track, 'unliked')
      if (backendAvailable.value) {
        void removeLikeTrack(track).catch(handleBackgroundError)
      }
    } else {
      likes.value = [track, ...likes.value]
      recordBehavior(track, 'liked')
      if (backendAvailable.value) {
        void addLikeTrack(track).catch(handleBackgroundError)
      }
    }
  }

  function moveLikeItem(from: number, to: number) {
    const len = likes.value.length
    if (from < 0 || from >= len || to < 0 || to >= len || from === to) return
    const nextLikes = [...likes.value]
    const [moved] = nextLikes.splice(from, 1)
    nextLikes.splice(to, 0, moved)
    likes.value = nextLikes
  }

  function createPlaylist(name: string, tracks: Track[] = []): Playlist {
    const playlist: Playlist = {
      id: `pl_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      name,
      cover: tracks[0]?.cover ?? null,
      sourceType: 'user-created',
      sourceBvid: null,
      tracks,
      createdAt: Date.now(),
    }
    playlists.value = [playlist, ...playlists.value]

    if (backendAvailable.value) {
      void createPlaylistRemote(name, tracks)
        .then((remote) => {
          playlists.value = playlists.value.map((item) => (item.id === playlist.id ? remote : item))
        })
        .catch(handleBackgroundError)
    }

    return playlist
  }

  function createCollection(
    name: string,
    tracks: Track[] = [],
    sourceType: Playlist['sourceType'] = 'user-created',
    sourceBvid?: string | null
  ): Playlist {
    const playlist: Playlist = {
      id: `pl_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      name,
      cover: tracks[0]?.cover ?? null,
      sourceType,
      sourceBvid: sourceBvid ?? null,
      tracks,
      createdAt: Date.now(),
    }
    playlists.value = [playlist, ...playlists.value]

    if (backendAvailable.value) {
      void createCollectionRemote(name, tracks, sourceType, sourceBvid, playlist.cover)
        .then((remote) => {
          playlists.value = playlists.value.map((item) => (item.id === playlist.id ? remote : item))
        })
        .catch(handleBackgroundError)
    }

    return playlist
  }

  function removePlaylist(id: string) {
    playlists.value = playlists.value.filter((p) => p.id !== id)
    if (backendAvailable.value) {
      void deletePlaylistRemote(id).catch(handleBackgroundError)
    }
  }

  function getPlaylist(id: string): Playlist | undefined {
    return playlists.value.find((p) => p.id === id)
  }

  function addToPlaylist(id: string, track: Track) {
    const playlist = playlists.value.find((p) => p.id === id)
    if (!playlist) return
    if (playlist.tracks.some((t) => isSameTrack(t, track))) return
    playlist.tracks.push(track)
    if (!playlist.cover) playlist.cover = track.cover
    recordBehavior(track, 'collection_added')
    if (backendAvailable.value) {
      void addPlaylistItemsRemote(id, [track]).catch(handleBackgroundError)
    }
  }

  function addTracksToPlaylist(id: string, tracks: Track[]) {
    const playlist = playlists.value.find((p) => p.id === id)
    if (!playlist || tracks.length === 0) return
    const toAdd = tracks.filter((track) => !playlist.tracks.some((existing) => isSameTrack(existing, track)))
    if (toAdd.length === 0) return
    playlist.tracks.push(...toAdd)
    if (!playlist.cover) playlist.cover = toAdd[0]?.cover ?? null
    for (const track of toAdd) {
      recordBehavior(track, 'collection_added')
    }
    if (backendAvailable.value) {
      void addPlaylistItemsRemote(id, toAdd).catch(handleBackgroundError)
    }
  }

  function updatePlaylist(id: string, payload: { name?: string; cover?: string | null }) {
    const playlist = playlists.value.find((p) => p.id === id)
    if (!playlist) return
    if (payload.name !== undefined) playlist.name = payload.name
    if (payload.cover !== undefined) playlist.cover = payload.cover
    if (backendAvailable.value) {
      void updatePlaylistRemote(id, payload)
        .then((remote) => {
          playlists.value = playlists.value.map((item) => (item.id === id ? remote : item))
        })
        .catch(handleBackgroundError)
    }
  }

  function replacePlaylistTracks(id: string, tracks: Track[]) {
    const playlist = playlists.value.find((p) => p.id === id)
    if (!playlist) return
    playlist.tracks = [...tracks]
    playlist.cover = tracks[0]?.cover ?? null
    if (backendAvailable.value) {
      void replacePlaylistItemsRemote(id, tracks)
        .then((result) => {
          playlists.value = playlists.value.map((item) => (item.id === id ? result.playlist : item))
        })
        .catch(handleBackgroundError)
    }
  }

  function hasPlaylistTrack(id: string, track: Track): boolean {
    const playlist = playlists.value.find((p) => p.id === id)
    return !!playlist && playlist.tracks.some((t) => isSameTrack(t, track))
  }

  async function migrateLocalFallback(
    localRecent: Track[],
    localLikes: Track[],
    localPlaylists: Playlist[],
    remoteRecent: Track[],
    remoteLikes: Track[],
    remotePlaylists: Playlist[]
  ): Promise<MigrationResult> {
    const recentToPush = localRecent.filter((track) => !remoteRecent.some((remote) => isSameTrack(remote, track)))
    const likesToPush = localLikes.filter((track) => !remoteLikes.some((remote) => isSameTrack(remote, track)))
    const playlistNames = new Set(remotePlaylists.map((playlist) => playlist.name))
    const playlistsToPush = localPlaylists.filter((playlist) => !playlistNames.has(playlist.name))

    const tasks: Array<() => Promise<unknown>> = [
      ...recentToPush.map((track) => () => addRecentTrack(track)),
      ...likesToPush.map((track) => () => addLikeTrack(track)),
      ...playlistsToPush.map((playlist) => () => createCollectionRemote(
        playlist.name,
        playlist.tracks,
        playlist.sourceType ?? 'user-created',
        playlist.sourceBvid ?? null,
        playlist.cover ?? null
      )),
    ]
    await runWithConcurrency(tasks, MIGRATION_CONCURRENCY)

    return {
      recent: recentToPush.length > 0,
      likes: likesToPush.length > 0,
      playlists: playlistsToPush.length > 0,
    }
  }

  function mergeTracks(primary: Track[], fallback: Track[]): Track[] {
    const result = [...primary]
    for (const track of fallback) {
      if (!result.some((item) => isSameTrack(item, track))) {
        result.push(track)
      }
    }
    return result
  }

  function mergePlaylists(primary: Playlist[], fallback: Playlist[]): Playlist[] {
    const result = [...primary]
    for (const playlist of fallback) {
      if (!result.some((item) => item.id === playlist.id || item.name === playlist.name)) {
        result.push(playlist)
      }
    }
    return result
  }

  function recordBehavior(track: Track, event: 'liked' | 'unliked' | 'collection_added') {
    const trackId = track.trackId ?? `${track.bvid}:${track.cid ?? 'main'}`
    void recordRecommendationEvent({
      trackId,
      event,
      scene: 'library',
      source: 'user-action',
      reason: 'library-behavior',
    }).catch(() => undefined)
  }

  function isSameTrack(a: Track, b: Track): boolean {
    if (a.trackId && b.trackId) return a.trackId === b.trackId
    if (a.cid != null || b.cid != null) return a.bvid === b.bvid && a.cid != null && b.cid != null && a.cid === b.cid
    return a.bvid === b.bvid
  }

  function handleBackgroundError(error: unknown) {
    backendAvailable.value = false
    syncError.value = error instanceof Error ? error.message : '本地库后台同步失败'
  }

  return {
    recent,
    likes,
    playlists,
    isSyncing,
    backendAvailable,
    syncError,
    initialize,
    refreshFromBackend,
    refreshRecent,
    updateRecentProgress,
    addRecent,
    clearRecent,
    removeRecent,
    isLiked,
    isTrackLiked,
    toggleLike,
    moveLikeItem,
    createPlaylist,
    createCollection,
    removePlaylist,
    getPlaylist,
    addToPlaylist,
    addTracksToPlaylist,
    updatePlaylist,
    replacePlaylistTracks,
    hasPlaylistTrack,
  }
})
