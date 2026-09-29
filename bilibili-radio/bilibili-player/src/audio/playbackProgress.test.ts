import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProgressOutbox, newerResume, playbackTrackId, resumeSeconds, PROGRESS_TTL_MS, type ProgressEvent } from './playbackProgress'

const track = { bvid: 'BV1Q541167Qg', cid: 123, title: 'episode', owner: '', cover: '', duration: 600 }
const event = (eventSeq = 1): ProgressEvent => ({ track, trackId: playbackTrackId(track), sessionId: 'a',
  sessionStartedAtMs: 100, eventSeq, event: 'pause', positionMs: 45000, listenMs: 5000,
  completed: false, lastActiveAtMs: Date.now(), lastPlayedAt: new Date().toISOString() })

beforeEach(() => localStorage.clear())
afterEach(() => vi.useRealTimers())

describe('durable playback outbox', () => {
  it('restores progress from disk after offline failure and retries later', async () => {
    const offline = new ProgressOutbox('alice', localStorage, vi.fn().mockRejectedValue(new Error('offline')))
    offline.save(event())
    await expect(offline.flush()).rejects.toThrow('offline')
    const send = vi.fn().mockResolvedValue({ accepted: true })
    const restarted = new ProgressOutbox('alice', localStorage, send)
    expect(restarted.read(event().trackId)?.positionMs).toBe(45000)
    await restarted.flush()
    await restarted.flush()
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('does not clear a newer seek when an older request completes', async () => {
    let acknowledge!: () => void
    const send = vi.fn().mockImplementationOnce(() => new Promise<void>(resolve => { acknowledge = resolve }))
      .mockResolvedValue({ accepted: true })
    const box = new ProgressOutbox('alice', localStorage, send)
    box.save(event())
    const sending = box.flush()
    box.save({ ...event(2), positionMs: 10000, event: 'seek' })
    acknowledge()
    await sending
    expect(send).toHaveBeenCalledTimes(2)
    expect(send.mock.calls[1][0].positionMs).toBe(10000)
    expect(box.read(event().trackId)?.eventSeq).toBe(2)
  })

  it('does not mix users or episodes', () => {
    const alice = new ProgressOutbox('alice', localStorage, vi.fn())
    alice.save(event())
    expect(new ProgressOutbox('bob', localStorage, vi.fn()).read(event().trackId)).toBeNull()
    expect(alice.read(playbackTrackId({ ...track, cid: 456 }))).toBeNull()
  })

  it('uses actual latest position including backwards seeks', () => {
    const local = { ...event(3), positionMs: 10000 }
    const remote = { ...event(2), positionMs: 80000 }
    expect(newerResume(local, remote)?.positionMs).toBe(10000)
  })

  it('prefers a newer remote session over stale local data', () => {
    expect(newerResume(event(), { ...event(), sessionId: 'b', sessionStartedAtMs: 200 })?.sessionId).toBe('b')
  })

  it('restarts completed content and clamps invalid or overlong positions', () => {
    expect(resumeSeconds({ ...event(), completed: true }, 600)).toBe(0)
    expect(resumeSeconds({ ...event(), positionMs: 900000 }, 600)).toBe(599.75)
    expect(resumeSeconds({ ...event(), positionMs: NaN }, 600)).toBe(0)
    expect(resumeSeconds(event(), 600)).toBe(45)
  })

  it('expires at 24 hours and removes expired offline events instead of uploading them', async () => {
    vi.useFakeTimers()
    const start = Date.now()
    const send = vi.fn()
    const box = new ProgressOutbox('alice', localStorage, send)
    box.save({ ...event(), positionMs: 134000 })
    vi.setSystemTime(start + PROGRESS_TTL_MS - 1)
    expect(resumeSeconds(box.read(event().trackId), 600)).toBe(134)
    vi.setSystemTime(start + PROGRESS_TTL_MS)
    const restarted = new ProgressOutbox('alice', localStorage, send)
    expect(restarted.read(event().trackId)).toBeNull()
    await restarted.flush()
    expect(send).not.toHaveBeenCalled()
    expect(localStorage.getItem(restarted.key)).toBe('{}')
  })

  it('does not let a stale cache beat a fresh remote point or an expired remote zero', () => {
    const stale = { ...event(99), lastActiveAtMs: Date.now() - PROGRESS_TTL_MS }
    expect(newerResume(stale, null)).toBeNull()
    expect(newerResume(stale, { ...event(1), positionMs: 261000 })?.positionMs).toBe(261000)
    expect(resumeSeconds(stale, 600)).toBe(0)
  })

  it('keeps the original expiry when an offline upload is retried later', async () => {
    vi.useFakeTimers()
    const start = Date.now()
    const send = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ accepted: true })
    const box = new ProgressOutbox('alice', localStorage, send)
    box.save(event())
    await expect(box.flush()).rejects.toThrow()
    vi.setSystemTime(start + PROGRESS_TTL_MS - 1000)
    await box.flush()
    expect(send.mock.calls[1][0].lastActiveAtMs).toBe(start)
    vi.setSystemTime(start + PROGRESS_TTL_MS)
    expect(box.read(event().trackId)).toBeNull()
  })
})
