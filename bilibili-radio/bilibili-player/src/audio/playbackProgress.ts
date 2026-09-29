import type { Track } from '@/types'

export interface ResumePoint {
  trackId: string
  positionMs: number
  listenMs: number
  completed: boolean
  lastPlayedAt?: string | null
  sessionStartedAtMs?: number
  sessionId?: string
  eventSeq?: number
}

export interface ProgressEvent extends ResumePoint {
  track: Track
  sessionId: string
  sessionStartedAtMs: number
  eventSeq: number
  event: 'heartbeat' | 'pause' | 'seek' | 'change' | 'stop' | 'ended' | 'quit'
}

interface CachedProgress extends ProgressEvent { pending: boolean }

export function playbackTrackId(track: Track): string {
  return track.cid == null ? `bili:${track.bvid}` : `bili:${track.bvid}:cid:${track.cid}`
}

export function resumeSeconds(point: ResumePoint | null | undefined, duration: number): number {
  if (!point || point.completed || !Number.isFinite(point.positionMs)) return 0
  const value = Math.max(0, point.positionMs / 1000)
  return duration > 0 ? Math.min(value, Math.max(0, duration - 0.25)) : value
}

export function newerResume(local: ResumePoint | null, remote: ResumePoint | null): ResumePoint | null {
  if (!local) return remote
  if (!remote) return local
  if (local.sessionId && remote.sessionId && local.sessionId === remote.sessionId) {
    return (local.eventSeq ?? 0) > (remote.eventSeq ?? 0) ? local : remote
  }
  if (local.sessionStartedAtMs && remote.sessionStartedAtMs) {
    return local.sessionStartedAtMs > remote.sessionStartedAtMs ? local : remote
  }
  return Date.parse(local.lastPlayedAt ?? '') > Date.parse(remote.lastPlayedAt ?? '') ? local : remote
}

/** One scoped outbox. New checkpoints supersede older requests for the same track. */
export class ProgressOutbox {
  private entries: Record<string, CachedProgress> = {}
  private running: Promise<void> | null = null
  readonly key: string

  constructor(userId: string, private storage: Storage, private send: (event: ProgressEvent) => Promise<unknown>) {
    this.key = `bili-radio:progress:v1:${encodeURIComponent(userId)}`
    try {
      const parsed = JSON.parse(storage.getItem(this.key) || '{}')
      for (const value of Object.values(parsed) as CachedProgress[]) {
        if (value && typeof value.trackId === 'string' && Number.isFinite(value.positionMs)
          && Number.isFinite(value.sessionStartedAtMs) && Number.isFinite(value.eventSeq) && value.track) {
          this.entries[value.trackId] = value
        }
      }
    } catch { /* A corrupt local cache must not block playback. */ }
  }

  read(trackId: string): ResumePoint | null { return this.entries[trackId] ?? null }

  save(event: ProgressEvent): void {
    this.entries[event.trackId] = { ...event, pending: true }
    this.persist()
  }

  flush(): Promise<void> {
    if (this.running) return this.running
    this.running = this.drain().finally(() => { this.running = null })
    return this.running
  }

  private async drain(): Promise<void> {
    while (true) {
      const item = Object.values(this.entries).find(value => value.pending)
      if (!item) return
      await this.send(item)
      const current = this.entries[item.trackId]
      // A response for an older request cannot acknowledge a newer checkpoint.
      if (current.sessionId === item.sessionId && current.eventSeq === item.eventSeq) {
        current.pending = false
        this.persist()
      }
    }
  }

  private persist(): void {
    const settled = Object.values(this.entries).filter(item => !item.pending)
      .sort((a, b) => b.sessionStartedAtMs - a.sessionStartedAtMs)
    for (const item of settled.slice(300)) delete this.entries[item.trackId]
    this.storage.setItem(this.key, JSON.stringify(this.entries))
  }
}
