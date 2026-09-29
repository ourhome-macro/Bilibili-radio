import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ProgressOutbox, newerResume, playbackTrackId, resumeSeconds, type ProgressEvent } from './playbackProgress'

const track = { bvid: 'BV1Q541167Qg', cid: 123, title: 'episode', owner: '', cover: '', duration: 600 }
const event = (eventSeq = 1): ProgressEvent => ({ track, trackId: playbackTrackId(track), sessionId: 'a',
  sessionStartedAtMs: 100, eventSeq, event: 'pause', positionMs: 45000, listenMs: 5000,
  completed: false, lastPlayedAt: '2026-09-29T01:00:00Z' })

beforeEach(() => localStorage.clear())

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
})
