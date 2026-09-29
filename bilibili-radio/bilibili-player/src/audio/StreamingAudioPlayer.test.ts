import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { AudioStreamInfo } from '@/types'

class FakeAudio extends EventTarget {
  static current: FakeAudio
  currentTime = 0
  duration = 120
  readyState = 4
  src = ''
  volume = 1
  playbackRate = 1
  preservesPitch = true
  crossOrigin = ''
  load = vi.fn()
  pause = vi.fn()
  play = vi.fn().mockResolvedValue(undefined)
  removeAttribute() { this.src = '' }
  constructor() { super(); FakeAudio.current = this }
}
const stream: AudioStreamInfo = { url: 'https://fixture/audio', duration: 120, bitrate: 128000, sample_rate: 44100, channels: 1 }

beforeEach(() => { vi.resetModules(); vi.stubGlobal('Audio', FakeAudio) })
afterEach(() => vi.unstubAllGlobals())

it('does not signal playback readiness until initial resume seeking completes', async () => {
  const { streamingAudioPlayer: player } = await import('./StreamingAudioPlayer')
  player.init()
  const ready = vi.fn()
  player.onCanPlay(ready)
  player.loadStream(stream, 45)
  const audio = FakeAudio.current
  audio.dispatchEvent(new Event('canplay'))
  expect(ready).not.toHaveBeenCalled()
  audio.dispatchEvent(new Event('loadedmetadata'))
  expect(audio.currentTime).toBe(45)
  audio.dispatchEvent(new Event('canplay'))
  expect(ready).not.toHaveBeenCalled()
  audio.dispatchEvent(new Event('seeked'))
  expect(ready).toHaveBeenCalledTimes(1)
})

it('cancels an initial resume seek when switching to a fresh stream', async () => {
  const { streamingAudioPlayer: player } = await import('./StreamingAudioPlayer')
  player.init()
  const ready = vi.fn()
  player.onCanPlay(ready)
  player.loadStream(stream, 45)
  player.stop()
  player.loadStream({ ...stream, url: 'https://fixture/next' }, 0)
  FakeAudio.current.dispatchEvent(new Event('loadedmetadata'))
  FakeAudio.current.dispatchEvent(new Event('seeked'))
  expect(FakeAudio.current.currentTime).toBe(0)
  expect(ready).not.toHaveBeenCalled()
  FakeAudio.current.dispatchEvent(new Event('canplay'))
  expect(ready).toHaveBeenCalledTimes(1)
})
