import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type {
  VideoInfo,
  PlayerStatus,
  PlayMode,
  Track,
  PlayerQueueSnapshot,
  TrackSubtitleLine,
  AudioQualityPreference,
} from '@/types'
import {
  apiUrl,
  downloadTrackToLocalFile,
  fetchSettings,
  fetchPlayerQueue,
  getTrackDetail,
  getTrackCoverInfo,
  getTrackSubtitles,
  getTrackStreamInfo,
  resolveTrackInput,
  savePlayerQueue,
  updateSettings,
  recordRecommendationEvent,
} from '@/api/client'
import { streamingAudioPlayer } from '@/audio/StreamingAudioPlayer'
import { isDesktopRuntime } from '@/desktop/runtime'
import { useLibraryStore } from '@/stores/libraryStore'

const PLAY_MODES: PlayMode[] = ['order', 'loop', 'single', 'shuffle']
const AUDIO_QUALITIES: AudioQualityPreference[] = ['auto', '64k', '132k', '192k', 'dolby', 'hires']
const PLAYBACK_SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2]
const QUEUE_STORAGE_KEY = 'bili-radio:player-queue'
const SETTINGS_STORAGE_KEY = 'bili-radio:player-settings'
const QUEUE_SAVE_DEBOUNCE_MS = 300
const RECENT_RECORD_RATIO = 0.1
const QUICK_SKIP_SECONDS = 15

function isPlayMode(value: unknown): value is PlayMode {
  return typeof value === 'string' && PLAY_MODES.includes(value as PlayMode)
}

function isAudioQuality(value: unknown): value is AudioQualityPreference {
  return typeof value === 'string' && AUDIO_QUALITIES.includes(value as AudioQualityPreference)
}

function isPlaybackSpeed(value: unknown): value is number {
  return typeof value === 'number' && PLAYBACK_SPEEDS.includes(value)
}

function loadQueueSnapshot(): PlayerQueueSnapshot {
  try {
    const raw = localStorage.getItem(QUEUE_STORAGE_KEY)
    if (!raw) {
      return { queue: [], currentIndex: -1, playMode: 'order', updatedAt: null }
    }
    const parsed = JSON.parse(raw) as Partial<PlayerQueueSnapshot>
    const queue = Array.isArray(parsed.queue) ? parsed.queue.filter(isValidTrack) : []
    return {
      queue,
      currentIndex: clampQueueIndex(Number(parsed.currentIndex ?? -1), queue.length),
      playMode: isPlayMode(parsed.playMode) ? parsed.playMode : 'order',
      updatedAt: parsed.updatedAt ?? null,
    }
  } catch {
    return { queue: [], currentIndex: -1, playMode: 'order', updatedAt: null }
  }
}

function loadSettingsSnapshot(): { audioQualityPreference: AudioQualityPreference; playbackSpeed: number } {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    const playbackSpeed = Number(parsed.playbackSpeed)
    return {
      audioQualityPreference: isAudioQuality(parsed.audioQualityPreference) ? parsed.audioQualityPreference : 'auto',
      playbackSpeed: isPlaybackSpeed(playbackSpeed) ? playbackSpeed : 1,
    }
  } catch {
    return { audioQualityPreference: 'auto', playbackSpeed: 1 }
  }
}

function isValidTrack(value: unknown): value is Track {
  const track = value as Track
  return !!track && typeof track.bvid === 'string' && typeof track.title === 'string'
}

function clampQueueIndex(index: number, queueLength: number): number {
  if (queueLength <= 0) return -1
  if (!Number.isFinite(index)) return -1
  return Math.max(-1, Math.min(Math.trunc(index), queueLength - 1))
}

export const usePlayerStore = defineStore('player', () => {
  const initialQueueSnapshot = loadQueueSnapshot()
  const initialSettingsSnapshot = loadSettingsSnapshot()
  const status = ref<PlayerStatus>('idle')
  const currentTime = ref(0)
  const duration = ref(0)
  const volume = ref(0.8)
  const isMuted = ref(false)
  const bufferLevel = ref(0)
  const videoInfo = ref<VideoInfo | null>(null)
  const errorMessage = ref<string | null>(null)
  const statusMessage = ref<string>('')
  const isInitialized = ref(false)
  const isDownloading = ref(false)
  const subtitleLines = ref<TrackSubtitleLine[]>([])
  const subtitleTrackKey = ref('')
  const subtitleLoading = ref(false)
  const subtitleError = ref<string | null>(null)
  const playRequestSerial = ref(0)
  const audioQualityPreference = ref<AudioQualityPreference>(initialSettingsSnapshot.audioQualityPreference)
  const availableAudioQualities = ref<AudioQualityPreference[]>(['auto'])
  const playbackSpeed = ref(initialSettingsSnapshot.playbackSpeed)
  const settingsBackendAvailable = ref(false)
  const settingsSyncError = ref<string | null>(null)

  // 播放队列
  const queue = ref<Track[]>(initialQueueSnapshot.queue)
  const currentIndex = ref(initialQueueSnapshot.currentIndex)
  const playMode = ref<PlayMode>(initialQueueSnapshot.playMode)
  const queueBackendAvailable = ref(false)
  const queueSyncError = ref<string | null>(null)

  let playSeq = 0
  let initializePromise: Promise<void> | null = null
  let queueSaveTimer: ReturnType<typeof setTimeout> | null = null
  let queueRestored = false
  let suppressQueueRemoteSync = false
  let subtitleSeq = 0
  let shuffleHistory: number[] = []
  let shuffleFuture: number[] = []
  let playbackRecentKey: string | null = null
  let playbackRecentRecorded = false
  let playbackListenSeconds = 0
  let playbackLastPosition: number | null = null
  let autoplaySeq: number | null = null
  let playbackBehaviorRecorded = false

  const currentTrack = computed<Track | null>(() => {
    if (currentIndex.value < 0 || currentIndex.value >= queue.value.length) return null
    return queue.value[currentIndex.value]
  })
  const formattedCurrentTime = computed(() => formatTime(currentTime.value))
  const formattedDuration = computed(() => formatTime(duration.value))
  const progress = computed(() => {
    if (duration.value === 0) return 0
    return (currentTime.value / duration.value) * 100
  })
  const bufferPercent = computed(() => bufferLevel.value * 100)
  const isPlaying = computed(() => status.value === 'playing')
  const isPaused = computed(() => status.value === 'paused')
  const isLoading = computed(() => status.value === 'loading')
  const hasError = computed(() => status.value === 'error')
  const hasTrack = computed(() => currentTrack.value !== null || videoInfo.value !== null)
  const activeSubtitleLine = computed(() => {
    const index = findActiveSubtitleLineIndex(subtitleLines.value, currentTime.value)
    return index >= 0 ? subtitleLines.value[index] : null
  })
  const desktopLyricText = computed(() => activeSubtitleLine.value?.text || '-')

  watch(
    [queue, currentIndex, playMode],
    () => {
      const snapshot = currentQueueSnapshot()
      saveQueueSnapshotLocal(snapshot)
      if (queueRestored && queueBackendAvailable.value && !suppressQueueRemoteSync) {
        scheduleQueueRemoteSave(snapshot)
      }
    },
    { deep: true }
  )

  function currentQueueSnapshot(updatedAt = new Date().toISOString()): PlayerQueueSnapshot {
    const queueItems = queue.value.filter(isValidTrack)
    return {
      queue: queueItems,
      currentIndex: clampQueueIndex(currentIndex.value, queueItems.length),
      playMode: isPlayMode(playMode.value) ? playMode.value : 'order',
      updatedAt,
    }
  }

  function saveQueueSnapshotLocal(snapshot: PlayerQueueSnapshot) {
    localStorage.setItem(QUEUE_STORAGE_KEY, JSON.stringify(snapshot))
  }

  function applyQueueSnapshot(snapshot: PlayerQueueSnapshot) {
    suppressQueueRemoteSync = true
    const tracks = Array.isArray(snapshot.queue) ? snapshot.queue.filter(isValidTrack) : []
    queue.value = tracks
    currentIndex.value = clampQueueIndex(snapshot.currentIndex, tracks.length)
    playMode.value = isPlayMode(snapshot.playMode) ? snapshot.playMode : 'order'
    saveQueueSnapshotLocal({
      queue: tracks,
      currentIndex: currentIndex.value,
      playMode: playMode.value,
      updatedAt: snapshot.updatedAt ?? new Date().toISOString(),
    })
    syncRestoredCurrentTrack()
    window.setTimeout(() => {
      suppressQueueRemoteSync = false
    }, 0)
  }

  async function restorePersistedQueue() {
    if (queueRestored) return

    const localSnapshot = currentQueueSnapshot(initialQueueSnapshot.updatedAt ?? new Date().toISOString())
    try {
      const remoteSnapshot = await fetchPlayerQueue()
      queueBackendAvailable.value = true
      queueSyncError.value = null
      if (remoteSnapshot.updatedAt) {
        applyQueueSnapshot(remoteSnapshot)
      } else if (localSnapshot.queue.length > 0) {
        const saved = await savePlayerQueue(localSnapshot)
        applyQueueSnapshot(saved)
      } else {
        applyQueueSnapshot(localSnapshot)
      }
    } catch (error) {
      queueBackendAvailable.value = false
      queueSyncError.value = error instanceof Error ? error.message : '播放队列同步失败'
      applyQueueSnapshot(localSnapshot)
    } finally {
      queueRestored = true
    }
  }

  function scheduleQueueRemoteSave(snapshot = currentQueueSnapshot()) {
    if (queueSaveTimer) {
      clearTimeout(queueSaveTimer)
    }
    queueSaveTimer = setTimeout(() => {
      queueSaveTimer = null
      void persistQueueRemote(snapshot)
    }, QUEUE_SAVE_DEBOUNCE_MS)
  }

  async function persistQueueRemote(snapshot = currentQueueSnapshot()) {
    try {
      const saved = await savePlayerQueue(snapshot)
      queueBackendAvailable.value = true
      queueSyncError.value = null
      saveQueueSnapshotLocal({
        queue: saved.queue,
        currentIndex: saved.currentIndex,
        playMode: saved.playMode,
        updatedAt: saved.updatedAt ?? snapshot.updatedAt,
      })
    } catch (error) {
      queueBackendAvailable.value = false
      queueSyncError.value = error instanceof Error ? error.message : '播放队列同步失败'
    }
  }

  function syncRestoredCurrentTrack() {
    const track = currentTrack.value
    if (!track) {
      videoInfo.value = null
      duration.value = 0
      return
    }
    videoInfo.value = trackToVideoInfo(track)
    duration.value = track.duration
  }

  function formatTime(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds < 0) seconds = 0
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }

  function initialize(): Promise<void> {
    if (isInitialized.value) return Promise.resolve()
    if (initializePromise) return initializePromise

    initializePromise = initializePlayer().finally(() => {
      initializePromise = null
    })
    return initializePromise
  }

  async function initializePlayer() {
    await restorePersistedQueue()
    if (isInitialized.value) return

    const playerReady = streamingAudioPlayer.init()
    if (!playerReady) {
      setError('音频播放器初始化失败')
      return
    }

    streamingAudioPlayer.onStateChange((playing) => {
      if (playing) {
        status.value = 'playing'
      } else {
        if (status.value === 'playing') {
          status.value = 'paused'
        }
      }
    })

    streamingAudioPlayer.onTimeUpdate((time, dur) => {
      accumulatePlaybackListenTime(time)
      currentTime.value = time
      if (dur > 0 && dur !== duration.value) {
        duration.value = dur
      }
      maybeRecordRecentProgress(false)
    })

    streamingAudioPlayer.onEnded(() => {
      handleTrackEnded()
    })

    streamingAudioPlayer.onError((error) => {
      setError(error)
    })

    streamingAudioPlayer.onCanPlay(() => {
      if (autoplaySeq !== playSeq || status.value !== 'loading') return
      autoplaySeq = null
      streamingAudioPlayer.play()
    })

    streamingAudioPlayer.setVolume(volume.value)
    streamingAudioPlayer.setPlaybackRate(playbackSpeed.value)
    void restorePersistedSettings()

    isInitialized.value = true
  }

  async function restorePersistedSettings() {
    try {
      const settings = await fetchSettings()
      settingsBackendAvailable.value = true
      settingsSyncError.value = null
      audioQualityPreference.value = isAudioQuality(settings.audioQualityPreference)
        ? settings.audioQualityPreference
        : audioQualityPreference.value
      playbackSpeed.value = isPlaybackSpeed(Number(settings.playbackSpeed)) ? Number(settings.playbackSpeed) : 1
      saveSettingsSnapshotLocal()
      streamingAudioPlayer.setPlaybackRate(playbackSpeed.value)
    } catch (error) {
      settingsBackendAvailable.value = false
      settingsSyncError.value = error instanceof Error ? error.message : 'settings sync failed'
      streamingAudioPlayer.setPlaybackRate(playbackSpeed.value)
    }
  }

  function saveSettingsSnapshotLocal() {
    localStorage.setItem(
      SETTINGS_STORAGE_KEY,
      JSON.stringify({
        audioQualityPreference: audioQualityPreference.value,
        playbackSpeed: playbackSpeed.value,
      })
    )
  }

  function setError(message: string) {
    errorMessage.value = message
    status.value = 'error'
  }

  function clearError() {
    errorMessage.value = null
  }

  async function requestPlayTrack(track: Track) {
    const seq = ++playSeq
    playRequestSerial.value = seq
    recordPlaybackBehaviorBeforeTrackChange()
    clearError()
    status.value = 'loading'
    statusMessage.value = '正在获取视频信息...'
    currentTime.value = 0
    playbackRecentKey = null
    playbackRecentRecorded = false
    playbackListenSeconds = 0
    playbackLastPosition = null
    autoplaySeq = null
    playbackBehaviorRecorded = false
    clearCurrentSubtitles()
    streamingAudioPlayer.stop()

    await initialize()
    if (!isInitialized.value || seq !== playSeq) return

    try {
      syncQueueCurrentTrack(track)
      videoInfo.value = trackToVideoInfo(track)
      duration.value = track.duration
      statusMessage.value = '正在解析音频流...'

      const streamInfo = await getTrackStreamInfo(track.bvid, track.cid, audioQualityPreference.value)
      if (seq !== playSeq) return
      availableAudioQualities.value = normalizeAvailableQualities(streamInfo.availableAudioQualities)

      const resolvedCid = streamInfo.cid ?? track.cid
      const playableTrack: Track = {
        ...track,
        trackId: resolvedCid !== track.cid ? undefined : track.trackId,
        cid: resolvedCid,
        duration: streamInfo.duration || track.duration,
      }
      syncQueueCurrentTrack(playableTrack)
      videoInfo.value = trackToVideoInfo(playableTrack)
      duration.value = playableTrack.duration
      playbackRecentKey = trackIdentity(playableTrack)
      playbackRecentRecorded = false
      playbackListenSeconds = 0
      playbackLastPosition = null
      playbackBehaviorRecorded = false

      statusMessage.value = '正在缓冲音频...'
      autoplaySeq = seq
      streamingAudioPlayer.loadStream(streamInfo)
      hydrateTrackMetadataInBackground(playableTrack, seq)
    } catch (error) {
      if (seq !== playSeq) return
      setError(error instanceof Error ? error.message : '播放失败')
    }
  }

  /** 直接输入 BV/URL 播放：作为新曲目加入队列并播放 */
  async function playInput(input: string) {
    const value = input.trim()
    if (!value) {
      setError('请输入BV号或视频链接')
      return
    }
    clearError()
    status.value = 'loading'
    statusMessage.value = '正在解析输入...'
    try {
      const detail = await resolveTrackInput(value)
      const tracks = detail.pages.length > 1 ? detail.pages : [detail.pages[0] ?? detail.track]
      playList(tracks, 0)
    } catch (error) {
      setError(error instanceof Error ? error.message : '无法解析输入')
    }
  }

  /** 播放一条已知曲目（来自搜索结果、收藏、歌单等），若不在队列则追加 */
  function playTrack(track: Track) {
    const idx = findTrackIndex(track)
    if (idx >= 0) {
      currentIndex.value = idx
    } else {
      queue.value.push(track)
      resetShuffleFutureAfterQueueChange()
      currentIndex.value = queue.value.length - 1
    }
    markShuffleManualSelection(currentIndex.value)
    void requestPlayTrack(track)
  }

  /** 用一组曲目替换队列并从指定位置开始播放 */
  function playList(tracks: Track[], startIndex = 0) {
    if (tracks.length === 0) return
    queue.value = [...tracks]
    currentIndex.value = Math.max(0, Math.min(startIndex, tracks.length - 1))
    resetShuffleState()
    void requestPlayTrack(queue.value[currentIndex.value])
  }

  /** 追加到队列末尾，不打断当前播放 */
  function enqueue(track: Track) {
    if (queue.value.some((t) => isSameTrack(t, track))) return false
    queue.value.push(track)
    resetShuffleFutureAfterQueueChange()
    if (currentIndex.value === -1) {
      currentIndex.value = 0
      void requestPlayTrack(track)
    }
    return true
  }

  function enqueueTracks(tracks: Track[]) {
    const toAdd = tracks.filter((track) => !queue.value.some((candidate) => isSameTrack(candidate, track)))
    if (toAdd.length === 0) return 0
    const shouldStart = currentIndex.value === -1
    queue.value.push(...toAdd)
    resetShuffleFutureAfterQueueChange()
    if (shouldStart) {
      currentIndex.value = queue.value.length - toAdd.length
      void requestPlayTrack(queue.value[currentIndex.value])
    }
    return toAdd.length
  }

  function playAt(index: number) {
    if (index < 0 || index >= queue.value.length) return
    currentIndex.value = index
    markShuffleManualSelection(index)
    void requestPlayTrack(queue.value[index])
  }

  function removeFromQueue(index: number) {
    if (index < 0 || index >= queue.value.length) return
    queue.value.splice(index, 1)
    if (index < currentIndex.value) {
      currentIndex.value--
    } else if (index === currentIndex.value) {
      // 删除的是当前曲目：停止并将索引钳制到合法范围
      if (queue.value.length === 0) {
        stop()
      } else {
        currentIndex.value = Math.min(currentIndex.value, queue.value.length - 1)
        syncRestoredCurrentTrack()
      }
    }
    if (queue.value.length > 0) {
      reindexShuffleStateAfterRemoval(index)
    }
  }

  function moveQueueItem(from: number, to: number) {
    const len = queue.value.length
    if (from < 0 || from >= len || to < 0 || to >= len || from === to) return
    const previousCurrentIndex = currentIndex.value
    const nextQueue = [...queue.value]
    const [moved] = nextQueue.splice(from, 1)
    nextQueue.splice(to, 0, moved)
    queue.value = nextQueue

    if (previousCurrentIndex === from) {
      currentIndex.value = to
    } else if (from < previousCurrentIndex && previousCurrentIndex <= to) {
      currentIndex.value = previousCurrentIndex - 1
    } else if (to <= previousCurrentIndex && previousCurrentIndex < from) {
      currentIndex.value = previousCurrentIndex + 1
    } else {
      currentIndex.value = previousCurrentIndex
    }
    resetShuffleState()
  }

  function clearQueue() {
    stop()
    queue.value = []
    currentIndex.value = -1
    resetShuffleState()
  }

  function nextIndex(): number {
    const len = queue.value.length
    if (len === 0) return -1
    if (playMode.value === 'shuffle') {
      if (len === 1) return currentIndex.value
      normalizeShuffleStateAfterQueueMutation()
      if (shuffleFuture.length === 0) {
        shuffleFuture = shuffledQueueIndices(currentIndex.value)
      }
      const next = shuffleFuture.shift()
      if (next == null) return currentIndex.value
      if (currentIndex.value >= 0 && currentIndex.value !== next) {
        shuffleHistory.push(currentIndex.value)
      }
      return next
    }
    const next = currentIndex.value + 1
    if (next >= len) {
      return 0
    }
    return next
  }

  function prevIndex(): number {
    const len = queue.value.length
    if (len === 0) return -1
    if (playMode.value === 'shuffle') {
      if (len === 1) return currentIndex.value
      normalizeShuffleStateAfterQueueMutation()
      const previous = shuffleHistory.pop()
      if (previous == null) {
        return currentIndex.value
      }
      if (currentIndex.value >= 0 && currentIndex.value !== previous) {
        shuffleFuture.unshift(currentIndex.value)
      }
      return previous
    }
    const prev = currentIndex.value - 1
    if (prev < 0) {
      return playMode.value === 'loop' ? len - 1 : 0
    }
    return prev
  }

  function next() {
    const n = nextIndex()
    if (n === -1) return
    playAt(n)
  }

  function prev() {
    // 播放超过 3 秒时，上一首先回到开头
    if (currentTime.value > 3) {
      seek(0)
      return
    }
    const p = prevIndex()
    if (p === -1) return
    playAt(p)
  }

  function handleTrackEnded() {
    maybeRecordRecentProgress(true)
    recordPlaybackBehavior('completed')
    if (playMode.value === 'single') {
      // 单曲循环：重新播放当前曲目
      const track = currentTrack.value
      if (track) {
        void requestPlayTrack(track)
        return
      }
    }
    next()
  }

  function resetShuffleState() {
    shuffleHistory = []
    shuffleFuture = playMode.value === 'shuffle' ? shuffledQueueIndices(currentIndex.value) : []
  }

  function resetShuffleFutureAfterQueueChange() {
    if (playMode.value !== 'shuffle') return
    shuffleFuture = shuffledQueueIndices(currentIndex.value)
  }

  function normalizeShuffleStateAfterQueueMutation() {
    const len = queue.value.length
    if (len <= 0) {
      shuffleHistory = []
      shuffleFuture = []
      return
    }
    const isValid = (index: number) => index >= 0 && index < len
    shuffleHistory = shuffleHistory.filter(isValid)
    shuffleFuture = dedupeIndices(shuffleFuture.filter((index) => isValid(index) && index !== currentIndex.value))
  }

  function reindexShuffleStateAfterRemoval(removedIndex: number) {
    const reindex = (index: number) => {
      if (index === removedIndex) return null
      return index > removedIndex ? index - 1 : index
    }
    shuffleHistory = shuffleHistory
      .map(reindex)
      .filter((index): index is number => index !== null)
    shuffleFuture = shuffleFuture
      .map(reindex)
      .filter((index): index is number => index !== null)
    normalizeShuffleStateAfterQueueMutation()
  }

  function markShuffleManualSelection(index: number) {
    if (playMode.value !== 'shuffle') return
    shuffleFuture = shuffleFuture.filter((candidate) => candidate !== index)
  }

  function shuffledQueueIndices(excludeIndex: number): number[] {
    const indices = queue.value
      .map((_, index) => index)
      .filter((index) => index !== excludeIndex)
    for (let i = indices.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1))
      ;[indices[i], indices[j]] = [indices[j], indices[i]]
    }
    return indices
  }

  function dedupeIndices(indices: number[]): number[] {
    const seen = new Set<number>()
    const result: number[] = []
    for (const index of indices) {
      if (seen.has(index)) continue
      seen.add(index)
      result.push(index)
    }
    return result
  }

  function cyclePlayMode() {
    const idx = PLAY_MODES.indexOf(playMode.value)
    playMode.value = PLAY_MODES[(idx + 1) % PLAY_MODES.length]
    resetShuffleState()
  }

  function setPlayMode(mode: PlayMode) {
    playMode.value = mode
    resetShuffleState()
  }

  function togglePlayPause() {
    if (status.value === 'playing') {
      pause()
    } else if (status.value === 'paused') {
      resume()
    } else if (currentTrack.value) {
      // 已停止但队列有曲目：重新播放当前曲目
      void requestPlayTrack(currentTrack.value)
    }
  }

  function pause() {
    autoplaySeq = null
    streamingAudioPlayer.pause()
    status.value = 'paused'
  }

  function resume() {
    streamingAudioPlayer.resume()
    status.value = 'playing'
  }

  function stop() {
    maybeRecordRecentProgress(false)
    recordPlaybackBehaviorBeforeTrackChange()
    playSeq++
    autoplaySeq = null
    streamingAudioPlayer.stop()
    status.value = 'idle'
    currentTime.value = 0
    videoInfo.value = null
    duration.value = 0
    statusMessage.value = ''
    clearCurrentSubtitles()
  }

  function seek(timeSeconds: number) {
    streamingAudioPlayer.seek(timeSeconds)
    currentTime.value = timeSeconds
  }

  function setVolume(value: number) {
    volume.value = value
    streamingAudioPlayer.setVolume(value)
  }

  function toggleMute() {
    isMuted.value = streamingAudioPlayer.toggleMute()
  }

  /** 把文件名里的非法字符替换成下划线 */
  function sanitizeFilename(name: string): string {
    return name.replace(/[\\/:*?"<>|]/g, '_').slice(0, 100).trim() || 'audio'
  }

  /**
   * 下载当前曲目的音频。按当前 Track 的 bvid/cid 解析代理流，
   * 不依赖这首歌是否已经播放过。
   */
  async function downloadCurrent() {
    const track = currentTrack.value
    const info = videoInfo.value
    const fallbackTrack = track ?? (info ? videoInfoToTrack(info) : null)
    if (!fallbackTrack) {
      setError('没有可下载的曲目')
      return
    }
    if (isDownloading.value) return

    isDownloading.value = true
    statusMessage.value = '正在下载音频...'
    try {
      if (isDesktopRuntime()) {
        const result = await downloadTrackToLocalFile(fallbackTrack, audioQualityPreference.value)
        statusMessage.value = `已保存到 ${result.path}`
        return
      }

      const streamInfo = await getTrackStreamInfo(fallbackTrack.bvid, fallbackTrack.cid, audioQualityPreference.value)
      const response = await fetch(apiUrl(streamInfo.url))
      if (!response.ok) {
        throw new Error(`下载失败（${response.status}）`)
      }
      const blob = await response.blob()
      // 从响应头推断扩展名，默认 .m4a（B站音频流通常是 m4a/aac）
      const contentType = response.headers.get('Content-Type') ?? ''
      const ext = contentType.includes('mp4') || contentType.includes('m4a')
        ? 'm4a'
        : contentType.includes('mpeg')
          ? 'mp3'
          : 'm4a'

      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${sanitizeFilename(fallbackTrack.title)}.${ext}`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      statusMessage.value = '下载完成'
    } catch (error) {
      setError(error instanceof Error ? error.message : '下载失败')
    } finally {
      isDownloading.value = false
    }
  }

  async function loadCurrentSubtitles(force = false) {
    const track = currentTrack.value ?? (videoInfo.value ? videoInfoToTrack(videoInfo.value) : null)
    if (!track?.bvid) {
      clearCurrentSubtitles()
      return
    }

    const key = subtitleKey(track)
    if (!force && subtitleTrackKey.value === key && (subtitleLines.value.length > 0 || subtitleLoading.value)) {
      return
    }

    const seq = ++subtitleSeq
    subtitleTrackKey.value = key
    subtitleLines.value = []
    subtitleLoading.value = true
    subtitleError.value = null

    try {
      const data = await getTrackSubtitles(track.bvid, track.cid)
      if (seq !== subtitleSeq) return
      if (track.cid != null && data.cid !== track.cid) {
        throw new Error('subtitle cid mismatch')
      }
      subtitleLines.value = data.lines
    } catch (error) {
      if (seq !== subtitleSeq) return
      subtitleLines.value = []
      subtitleError.value = error instanceof Error ? error.message : 'subtitle unavailable'
    } finally {
      if (seq === subtitleSeq) {
        subtitleLoading.value = false
      }
    }
  }

  function clearCurrentSubtitles() {
    subtitleSeq++
    subtitleLines.value = []
    subtitleTrackKey.value = ''
    subtitleLoading.value = false
    subtitleError.value = null
  }

  function subtitleKey(track: Track): string {
    return `${track.bvid}:${track.cid ?? ''}`
  }

  function findActiveSubtitleLineIndex(lines: TrackSubtitleLine[], playbackTime: number): number {
    let low = 0
    let high = lines.length - 1
    let candidate = -1

    while (low <= high) {
      const middle = Math.floor((low + high) / 2)
      if (lines[middle].from <= playbackTime) {
        candidate = middle
        low = middle + 1
      } else {
        high = middle - 1
      }
    }

    return candidate >= 0 && playbackTime < lines[candidate].to ? candidate : -1
  }

  async function hydrateTrackMetadata(track: Track): Promise<Track> {
    let nextTrack = track
    try {
      const detail = await getTrackDetail(track.bvid)
      const detailTrack = track.cid != null
        ? detail.pages.find((page) => page.cid === track.cid) ?? detail.track
        : detail.track
      nextTrack = {
        ...nextTrack,
        owner: nextTrack.owner || detailTrack.owner || detail.track.owner,
        ownerMid: nextTrack.ownerMid ?? detailTrack.ownerMid ?? detail.track.ownerMid ?? null,
        cover: nextTrack.cover || detailTrack.cover || detail.track.cover,
        publishedAt: nextTrack.publishedAt ?? detailTrack.publishedAt ?? detail.track.publishedAt,
        playCount: nextTrack.playCount ?? detailTrack.playCount ?? detail.track.playCount,
      }
    } catch (error) {
      console.warn('Failed to hydrate track detail:', error)
    }

    if (track.cid == null) return nextTrack
    try {
      const coverInfo = await getTrackCoverInfo(track.bvid, track.cid)
      return {
        ...nextTrack,
        cover: coverInfo.cover || nextTrack.cover,
      }
    } catch (error) {
      console.warn('Failed to hydrate track cover:', error)
      return nextTrack
    }
  }

  function hydrateTrackMetadataInBackground(track: Track, seq: number) {
    void hydrateTrackMetadata(track).then((hydratedTrack) => {
      if (seq !== playSeq) return
      const activeTrack = currentTrack.value
      if (!activeTrack || !isSameTrack(activeTrack, track)) return
      if (
        hydratedTrack.cover === activeTrack.cover
        && hydratedTrack.ownerMid === activeTrack.ownerMid
        && hydratedTrack.owner === activeTrack.owner
      ) return

      const updatedTrack = { ...activeTrack, ...hydratedTrack }
      queue.value[currentIndex.value] = updatedTrack
      videoInfo.value = trackToVideoInfo(updatedTrack)
    })
  }

  function syncQueueCurrentTrack(track: Track) {
    const idx = currentIndex.value >= 0 ? currentIndex.value : findTrackIndex(track)
    if (idx >= 0 && idx < queue.value.length) {
      queue.value[idx] = { ...queue.value[idx], ...track }
      currentIndex.value = idx
    } else {
      queue.value.push(track)
      currentIndex.value = queue.value.length - 1
    }
  }

  function maybeRecordRecentProgress(completed: boolean) {
    if (playbackRecentRecorded) return
    const track = currentTrack.value
    if (!track) return
    const key = trackIdentity(track)
    if (!playbackRecentKey || playbackRecentKey !== key) return
    const dur = duration.value || track.duration || 0
    if (dur <= 0) return
    const requiredListenSeconds = Math.max(0, dur * RECENT_RECORD_RATIO - 1)
    if (playbackListenSeconds < requiredListenSeconds) return

    const watchedSeconds = Math.max(currentTime.value, playbackListenSeconds)
    const completedByPosition = completed && currentTime.value >= dur * 0.95

    playbackRecentRecorded = true
    useLibraryStore().addRecent(track, {
      positionMs: Math.round(watchedSeconds * 1000),
      listenMs: Math.round(playbackListenSeconds * 1000),
      completed: completedByPosition,
    })
    recordPlaybackBehavior(completedByPosition ? 'completed' : 'played')
  }

  function recordPlaybackBehaviorBeforeTrackChange() {
    const track = currentTrack.value
    if (!track || !playbackRecentKey || playbackBehaviorRecorded) return
    const dur = duration.value || track.duration || 0
    const listenedRatio = dur > 0 ? playbackListenSeconds / dur : 0
    if (playbackListenSeconds > 0 && playbackListenSeconds < QUICK_SKIP_SECONDS && listenedRatio < RECENT_RECORD_RATIO) {
      recordPlaybackBehavior('skipped')
    }
  }

  function recordPlaybackBehavior(event: 'played' | 'completed' | 'skipped') {
    const track = currentTrack.value
    if (!track || !playbackRecentKey) return
    if (playbackBehaviorRecorded && event !== 'completed') return
    const trackId = trackIdentity(track)
    if (playbackRecentKey !== trackId) return
    playbackBehaviorRecorded = true
    void recordRecommendationEvent({
      trackId,
      event,
      scene: 'player',
      source: 'playback',
      reason: 'playback-behavior',
      score: 0,
      playedSeconds: Math.round(playbackListenSeconds),
      completed: event === 'completed',
      skipped: event === 'skipped',
    }).catch(() => undefined)
  }

  function accumulatePlaybackListenTime(position: number) {
    if (playbackRecentRecorded) {
      playbackLastPosition = position
      return
    }
    if (playbackLastPosition === null) {
      playbackLastPosition = position
      return
    }
    const delta = position - playbackLastPosition
    playbackLastPosition = position
    if (status.value !== 'playing') return
    if (delta <= 0 || delta > 5) return
    playbackListenSeconds += delta
  }

  function trackIdentity(track: Track): string {
    return track.trackId ?? `${track.bvid}:${track.cid ?? 'main'}`
  }

  function findTrackIndex(track: Track): number {
    return queue.value.findIndex((candidate) => isSameTrack(candidate, track))
  }

  function isSameTrack(a: Track, b: Track): boolean {
    if (a.trackId && b.trackId) return a.trackId === b.trackId
    if (a.cid != null || b.cid != null) return a.bvid === b.bvid && a.cid != null && b.cid != null && a.cid === b.cid
    return a.bvid === b.bvid
  }

  function trackToVideoInfo(track: Track): VideoInfo {
    return {
      trackId: track.trackId,
      bvid: track.bvid,
      cid: track.cid,
      title: track.title,
      duration: track.duration,
      owner: track.owner,
      ownerMid: track.ownerMid,
      cover: track.cover,
      playCount: track.playCount,
      publishedAt: track.publishedAt,
    }
  }

  function videoInfoToTrack(info: VideoInfo): Track {
    return {
      trackId: info.trackId,
      bvid: info.bvid,
      cid: info.cid,
      title: info.title,
      duration: info.duration,
      owner: info.owner,
      ownerMid: info.ownerMid,
      cover: info.cover,
      playCount: info.playCount,
      publishedAt: info.publishedAt,
    }
  }

  function normalizeAvailableQualities(value: unknown): AudioQualityPreference[] {
    if (!Array.isArray(value)) return ['auto']
    const result = value.filter(isAudioQuality)
    return result.length ? result : ['auto']
  }

  function setAudioQualityPreference(value: AudioQualityPreference) {
    if (!isAudioQuality(value)) return
    audioQualityPreference.value = value
    saveSettingsSnapshotLocal()
    void updateSettings({ audioQualityPreference: value })
      .then((settings) => {
        settingsBackendAvailable.value = true
        settingsSyncError.value = null
        if (isAudioQuality(settings.audioQualityPreference)) {
          audioQualityPreference.value = settings.audioQualityPreference
          saveSettingsSnapshotLocal()
        }
      })
      .catch((error) => {
        settingsBackendAvailable.value = false
        settingsSyncError.value = error instanceof Error ? error.message : 'settings sync failed'
      })
  }

  function setPlaybackSpeed(value: number) {
    const normalized = Number(value)
    if (!isPlaybackSpeed(normalized)) return
    playbackSpeed.value = normalized
    streamingAudioPlayer.setPlaybackRate(normalized)
    saveSettingsSnapshotLocal()
    void updateSettings({ playbackSpeed: normalized })
      .then((settings) => {
        settingsBackendAvailable.value = true
        settingsSyncError.value = null
        if (isPlaybackSpeed(Number(settings.playbackSpeed))) {
          playbackSpeed.value = Number(settings.playbackSpeed)
          streamingAudioPlayer.setPlaybackRate(playbackSpeed.value)
          saveSettingsSnapshotLocal()
        }
      })
      .catch((error) => {
        settingsBackendAvailable.value = false
        settingsSyncError.value = error instanceof Error ? error.message : 'settings sync failed'
      })
  }

  function disconnect() {
    playSeq++
    streamingAudioPlayer.destroy()
    isInitialized.value = false
    status.value = 'idle'
  }

  return {
    status,
    currentTime,
    duration,
    volume,
    isMuted,
    bufferLevel,
    videoInfo,
    errorMessage,
    statusMessage,
    isInitialized,
    subtitleLines,
    subtitleLoading,
    subtitleError,
    playRequestSerial,
    audioQualityPreference,
    availableAudioQualities,
    playbackSpeed,
    settingsBackendAvailable,
    settingsSyncError,
    queue,
    currentIndex,
    playMode,
    queueBackendAvailable,
    queueSyncError,
    currentTrack,
    formattedCurrentTime,
    formattedDuration,
    progress,
    bufferPercent,
    isPlaying,
    isPaused,
    isLoading,
    hasError,
    hasTrack,
    activeSubtitleLine,
    desktopLyricText,
    initialize,
    playInput,
    playTrack,
    playList,
    enqueue,
    enqueueTracks,
    playAt,
    removeFromQueue,
    moveQueueItem,
    clearQueue,
    next,
    prev,
    cyclePlayMode,
    setPlayMode,
    togglePlayPause,
    pause,
    resume,
    stop,
    seek,
    setVolume,
    toggleMute,
    clearError,
    disconnect,
    isDownloading,
    downloadCurrent,
    loadCurrentSubtitles,
    clearCurrentSubtitles,
    setAudioQualityPreference,
    setPlaybackSpeed,
  }
})
