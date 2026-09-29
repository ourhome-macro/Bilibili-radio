import type { AudioStreamInfo } from '@/types'

class StreamingAudioPlayer {
  private audioElement: HTMLAudioElement | null = null
  private volume = 1.0
  private isMuted = false
  private playbackRate = 1.0
  private initialPosition = 0
  private initialSeekPending = false

  private _onStateChange: ((playing: boolean) => void) | null = null
  private _onTimeUpdate: ((currentTime: number, duration: number) => void) | null = null
  private _onEnded: (() => void) | null = null
  private _onError: ((error: string) => void) | null = null
  private _onCanPlay: (() => void) | null = null

  init(): boolean {
    if (isDesktopLyricsOverlayRoute()) {
      console.warn('[StreamingAudioPlayer] Refused to initialize audio in desktop lyrics overlay')
      return false
    }

    if (this.audioElement) {
      this.applyPlaybackRate()
      this.setVolume(this.volume)
      return true
    }

    try {
      this.audioElement = new Audio()
      this.audioElement.crossOrigin = 'anonymous'
      this.audioElement.volume = this.isMuted ? 0 : this.volume
      this.applyPlaybackRate()

      this.audioElement.addEventListener('play', () => {
        if (this._onStateChange) {
          this._onStateChange(true)
        }
      })

      this.audioElement.addEventListener('pause', () => {
        if (this._onStateChange) {
          this._onStateChange(false)
        }
      })

      this.audioElement.addEventListener('timeupdate', () => {
        if (this._onTimeUpdate && this.audioElement) {
          this._onTimeUpdate(this.audioElement.currentTime, this.audioElement.duration || 0)
        }
      })

      this.audioElement.addEventListener('ended', () => {
        if (this._onEnded) {
          this._onEnded()
        }
      })

      this.audioElement.addEventListener('error', () => {
        if (this._onError && this.audioElement) {
          const error = this.audioElement.error
          this._onError(error?.message || '音频播放错误')
        }
      })

      this.audioElement.addEventListener('canplay', () => {
        if (this._onCanPlay && !this.initialSeekPending) {
          this._onCanPlay()
        }
      })

      this.audioElement.addEventListener('loadedmetadata', () => {
        if (!this.audioElement || this.initialPosition <= 0) return
        const target = Math.min(this.initialPosition, Math.max(0, this.audioElement.duration - 0.25))
        this.initialPosition = 0
        this.initialSeekPending = Number.isFinite(target) && target > 0
        if (this.initialSeekPending) this.audioElement.currentTime = target
      })
      this.audioElement.addEventListener('seeked', () => {
        if (!this.initialSeekPending) return
        this.initialSeekPending = false
        if (this.audioElement && this.audioElement.readyState >= 2) this._onCanPlay?.()
      })

      return true
    } catch (error) {
      console.error('Failed to initialize StreamingAudioPlayer:', error)
      return false
    }
  }

  loadStream(streamInfo: AudioStreamInfo, position = 0) {
    if (!this.audioElement) {
      console.error('Audio element not initialized')
      return
    }

    console.log('[StreamingAudioPlayer] Loading stream:', streamInfo.url)
    this.initialPosition = position
    this.initialSeekPending = position > 0
    this.audioElement.src = streamInfo.url
    this.applyPlaybackRate()
    this.audioElement.load()
  }

  play(): boolean {
    if (!this.audioElement) {
      return false
    }

    this.audioElement.play().catch(error => {
      console.error('[StreamingAudioPlayer] Play error:', error)
      if (this._onError) {
        this._onError(error.message)
      }
    })
    return true
  }

  pause() {
    if (this.audioElement) {
      this.audioElement.pause()
    }
  }

  resume() {
    if (this.audioElement) {
      this.audioElement.play().catch(error => {
        console.error('[StreamingAudioPlayer] Resume error:', error)
      })
    }
  }

  stop() {
    this.initialPosition = 0
    this.initialSeekPending = false
    if (this.audioElement) {
      this.audioElement.pause()
      this.audioElement.currentTime = 0
      this.audioElement.removeAttribute('src')
      this.audioElement.load()
    }
  }

  seek(timeSeconds: number) {
    if (this.audioElement) {
      this.audioElement.currentTime = Math.max(0, Math.min(timeSeconds, this.audioElement.duration || 0))
    }
  }

  setVolume(volume: number) {
    this.volume = Math.max(0, Math.min(1, volume))
    if (this.audioElement && !this.isMuted) {
      this.audioElement.volume = this.volume
    }
  }

  setPlaybackRate(rate: number) {
    this.playbackRate = Math.max(0.5, Math.min(2, rate))
    this.applyPlaybackRate()
  }

  private applyPlaybackRate() {
    if (!this.audioElement) return
    this.audioElement.playbackRate = this.playbackRate
    const audio = this.audioElement as HTMLAudioElement & {
      preservesPitch?: boolean
      mozPreservesPitch?: boolean
      webkitPreservesPitch?: boolean
    }
    audio.preservesPitch = true
    audio.mozPreservesPitch = true
    audio.webkitPreservesPitch = true
  }

  getVolume(): number {
    return this.volume
  }

  mute() {
    this.isMuted = true
    if (this.audioElement) {
      this.audioElement.volume = 0
    }
  }

  unmute() {
    this.isMuted = false
    if (this.audioElement) {
      this.audioElement.volume = this.volume
    }
  }

  toggleMute(): boolean {
    if (this.isMuted) {
      this.unmute()
    } else {
      this.mute()
    }
    return this.isMuted
  }

  isMutedState(): boolean {
    return this.isMuted
  }

  getCurrentTime(): number {
    return this.audioElement?.currentTime || 0
  }

  getDuration(): number {
    return this.audioElement?.duration || 0
  }

  isPlaying(): boolean {
    return this.audioElement ? !this.audioElement.paused : false
  }

  onStateChange(callback: (playing: boolean) => void) {
    this._onStateChange = callback
  }

  onTimeUpdate(callback: (currentTime: number, duration: number) => void) {
    this._onTimeUpdate = callback
  }

  onEnded(callback: () => void) {
    this._onEnded = callback
  }

  onError(callback: (error: string) => void) {
    this._onError = callback
  }

  onCanPlay(callback: () => void) {
    this._onCanPlay = callback
  }

  destroy() {
    if (this.audioElement) {
      this.audioElement.pause()
      this.audioElement.src = ''
      this.audioElement = null
    }
  }
}

export const streamingAudioPlayer = new StreamingAudioPlayer()

function isDesktopLyricsOverlayRoute(): boolean {
  return window.location.hash.startsWith('#/desktop-lyrics')
}
