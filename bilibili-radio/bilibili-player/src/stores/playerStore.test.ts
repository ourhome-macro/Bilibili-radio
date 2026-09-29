import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { usePlayerStore } from './playerStore'

const mocks = vi.hoisted(() => ({
  api: { resume: vi.fn(), save: vi.fn(), stream: vi.fn() },
  audio: { time: 0, onPlay: null as null | ((playing: boolean) => void), onReady: null as null | (() => void),
    onTime: null as null | ((time: number, duration: number) => void), onEnd: null as null | (() => void),
    load: vi.fn(), play: vi.fn(), stop: vi.fn() },
}))
const tracks = [123, 456].map((cid, i) => ({ trackId: `bili:BV1Q541167Qg:cid:${cid}`, bvid: 'BV1Q541167Qg', cid,
  title: `Episode ${i}`, owner: '', cover: '', duration: 600 }))

vi.mock('@/stores/authStore', () => ({ useAuthStore: () => ({ appUser: { id: 'alice' } }) }))
vi.mock('@/stores/libraryStore', () => ({ useLibraryStore: () => ({ refreshRecent: vi.fn().mockResolvedValue(undefined), updateRecentProgress: vi.fn() }) }))
vi.mock('@/api/client', () => ({
  apiUrl: (path: string) => path,
  fetchPlayerQueue: vi.fn().mockImplementation(async () => ({ queue: tracks, currentIndex: 0, playMode: 'order', updatedAt: '2026-09-29' })),
  savePlayerQueue: vi.fn().mockImplementation(async (snapshot) => snapshot),
  fetchSettings: vi.fn().mockResolvedValue({ audioQualityPreference: 'auto', playbackSpeed: 1 }),
  fetchPlaybackResume: mocks.api.resume, savePlaybackProgress: mocks.api.save,
  getTrackStreamInfo: mocks.api.stream,
  getTrackDetail: vi.fn().mockImplementation(async () => ({ track: tracks[0], pages: tracks })),
  getTrackCoverInfo: vi.fn().mockResolvedValue({ cover: '' }),
  getTrackSubtitles: vi.fn().mockResolvedValue({ lines: [] }),
  recordRecommendationEvent: vi.fn().mockResolvedValue({}), updateSettings: vi.fn().mockResolvedValue({}),
  downloadTrackToLocalFile: vi.fn(), resolveTrackInput: vi.fn(),
}))
vi.mock('@/audio/StreamingAudioPlayer', () => ({ streamingAudioPlayer: {
  init: () => true,
  onStateChange: (callback: (playing: boolean) => void) => { mocks.audio.onPlay = callback },
  onCanPlay: (callback: () => void) => { mocks.audio.onReady = callback },
  onTimeUpdate: (callback: (time: number, duration: number) => void) => { mocks.audio.onTime = (time, duration) => { mocks.audio.time = time; callback(time, duration) } },
  onEnded: (callback: () => void) => { mocks.audio.onEnd = callback }, onError: vi.fn(),
  loadStream: mocks.audio.load, play: mocks.audio.play, stop: mocks.audio.stop,
  resume: () => mocks.audio.onPlay?.(true), pause: () => mocks.audio.onPlay?.(false),
  seek: (time: number) => { mocks.audio.time = time }, getCurrentTime: () => mocks.audio.time,
  setVolume: vi.fn(), setPlaybackRate: vi.fn(), destroy: vi.fn(), toggleMute: vi.fn(),
} }))

async function settle() { for (let i = 0; i < 30; i++) await Promise.resolve() }
let player: ReturnType<typeof usePlayerStore>

beforeEach(async () => {
  vi.useFakeTimers()
  localStorage.clear()
  setActivePinia(createPinia())
  mocks.api.resume.mockReset().mockImplementation(async (id) => ({ trackId: id, positionMs: 45000, listenMs: 0, completed: false, lastActiveAtMs: Date.now() }))
  mocks.api.save.mockReset().mockResolvedValue({ accepted: true })
  mocks.api.stream.mockReset().mockImplementation(async (_bvid, cid) => ({ cid, duration: 600, url: `https://fixture/${cid}` }))
  mocks.audio.load.mockReset().mockImplementation((_info, position) => { mocks.audio.time = position })
  mocks.audio.play.mockReset().mockImplementation(() => mocks.audio.onPlay?.(true))
  mocks.audio.stop.mockReset().mockImplementation(() => { mocks.audio.time = 0 })
  player = usePlayerStore()
  await player.initialize()
})
afterEach(async () => { player.disconnect(); await settle(); player.$dispose(); vi.useRealTimers() })

describe('player control and resume integration', () => {
  it('restores paused preview and starts at the saved position only when media is ready', async () => {
    expect(player.currentTime).toBe(45)
    expect(player.status).toBe('idle')
    player.togglePlayPause()
    await settle()
    expect(mocks.audio.load).toHaveBeenLastCalledWith(expect.anything(), 45)
    expect(mocks.audio.play).not.toHaveBeenCalled()
    mocks.audio.onReady?.()
    expect(player.status).toBe('playing')
    expect(player.currentTime).toBe(45)
  })

  it('switches to the previous episode on the first click after 3 seconds', async () => {
    player.playAt(1); await settle(); mocks.audio.onReady?.()
    player.currentTime = 40
    mocks.audio.time = 40
    player.prev(); await settle()
    expect(player.currentIndex).toBe(0)
    expect(mocks.api.save).toHaveBeenCalledWith(expect.objectContaining({ trackId: tracks[1].trackId, positionMs: 40000, event: 'change' }))
    expect(mocks.audio.load).toHaveBeenCalledTimes(2)
  })

  it('saves short pauses and actual backwards seek positions', async () => {
    player.playAt(0); await settle(); mocks.audio.onReady?.()
    player.seek(5); player.pause(); await settle()
    expect(mocks.api.save).toHaveBeenLastCalledWith(expect.objectContaining({ event: 'pause', positionMs: 5000 }))
    player.seek(2); await settle()
    expect(mocks.api.save).toHaveBeenLastCalledWith(expect.objectContaining({ event: 'seek', positionMs: 2000 }))
  })

  it('does not reload the first track when there is no previous item', async () => {
    player.playAt(0); await settle(); mocks.audio.onReady?.()
    player.currentTime = 40
    player.prev(); await settle()
    expect(player.currentIndex).toBe(0)
    expect(player.currentTime).toBe(40)
    expect(mocks.audio.load).toHaveBeenCalledTimes(1)
  })

  it('wraps to the last track when previous is used in list-loop mode', async () => {
    player.playAt(0); await settle(); mocks.audio.onReady?.()
    player.setPlayMode('loop')
    player.currentTime = 40
    player.prev(); await settle()
    expect(player.currentIndex).toBe(1)
  })

  it('does not autoplay a stream that was paused while loading', async () => {
    player.playAt(0); await settle()
    player.pause()
    mocks.audio.onReady?.()
    expect(mocks.audio.play).not.toHaveBeenCalled()
    expect(player.status).toBe('paused')
  })

  it('persists heartbeat progress after the recent threshold has been reached', async () => {
    mocks.api.resume.mockResolvedValue({ positionMs: 0, completed: false })
    player.playAt(0); await settle(); mocks.audio.onReady?.()
    for (let t = 0; t <= 70; t++) mocks.audio.onTime?.(t, 600)
    await vi.advanceTimersByTimeAsync(5000)
    expect(mocks.api.save).toHaveBeenLastCalledWith(expect.objectContaining({ positionMs: 70000, listenMs: 70000 }))
  })

  it('ignores a late stream response after a fast track switch', async () => {
    let finishOld!: (info: unknown) => void
    mocks.api.stream.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
    player.playAt(0); await settle()
    player.playAt(1); await settle()
    finishOld({ cid: 123, duration: 600, url: 'https://old' }); await settle()
    expect(mocks.audio.load).toHaveBeenCalledTimes(1)
    expect(player.currentTrack?.cid).toBe(456)
  })

  it('single-loop completion restarts from zero, without overwriting the ended checkpoint', async () => {
    player.playAt(0); await settle(); mocks.audio.onReady?.()
    player.setPlayMode('single')
    player.currentTime = 600
    mocks.audio.time = 600
    mocks.audio.onEnd?.(); await settle()
    expect(mocks.audio.load).toHaveBeenLastCalledWith(expect.anything(), 0)
    expect(mocks.api.save).toHaveBeenCalledWith(expect.objectContaining({ event: 'ended', completed: true }))
  })

  it('flushes the latest checkpoint before desktop exit', async () => {
    player.playAt(0); await settle(); mocks.audio.onReady?.()
    player.currentTime = 123
    mocks.audio.time = 123
    await player.prepareForExit()
    expect(mocks.api.save).toHaveBeenLastCalledWith(expect.objectContaining({ event: 'quit', positionMs: 123000 }))
  })

  it('does not refresh paused progress through heartbeats or quitting', async () => {
    player.playAt(0); await settle(); mocks.audio.onReady?.()
    mocks.audio.onTime?.(134, 600)
    player.pause(); await settle()
    const activity = mocks.api.save.mock.calls.at(-1)?.[0].lastActiveAtMs
    const requests = mocks.api.save.mock.calls.length
    await vi.advanceTimersByTimeAsync(60_000)
    expect(mocks.api.save).toHaveBeenCalledTimes(requests)
    await player.prepareForExit()
    expect(mocks.api.save).toHaveBeenLastCalledWith(expect.objectContaining({ lastActiveAtMs: activity, positionMs: 134000 }))
  })

  it('rechecks expiry when resuming an already loaded player after a day', async () => {
    player.playAt(0); await settle(); mocks.audio.onReady?.()
    mocks.audio.onTime?.(134, 600)
    player.pause(); await settle()
    const saved = mocks.api.save.mock.calls.at(-1)?.[0]
    vi.setSystemTime(Date.now() + 86_400_001)
    mocks.api.resume.mockResolvedValue(saved) // even an old remote response cannot resurrect it
    player.resume(); await settle()
    expect(mocks.audio.load).toHaveBeenLastCalledWith(expect.anything(), 0)
  })

  it('actively replaying updates both the newest pointer and its deadline', async () => {
    player.playAt(0); await settle(); mocks.audio.onReady?.()
    mocks.audio.onTime?.(134, 600)
    player.pause(); await settle()
    const previous = mocks.api.save.mock.calls.at(-1)?.[0].lastActiveAtMs
    vi.setSystemTime(Date.now() + 3600_000)
    player.resume(); mocks.audio.onTime?.(261, 600); player.pause(); await settle()
    const latest = mocks.api.save.mock.calls.at(-1)?.[0]
    expect(latest.positionMs).toBe(261000)
    expect(latest.lastActiveAtMs).toBeGreaterThan(previous)
  })
})
