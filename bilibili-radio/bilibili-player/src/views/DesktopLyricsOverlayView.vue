<template>
  <main class="lyrics-window">
    <div class="lyrics-shell" :style="shellStyle" @mousedown="startWindowDrag">
      <div
        class="drag-border drag-border-top"
        title="拖动桌面歌词"
      />
      <div
        class="drag-border drag-border-right"
        title="拖动桌面歌词"
      />
      <div
        class="drag-border drag-border-bottom"
        title="拖动桌面歌词"
      />
      <div
        class="drag-border drag-border-left"
        title="拖动桌面歌词"
      />
      <p
        class="lyrics-text"
        :style="{ color: state.color }"
        :title="state.title || state.text"
        aria-live="polite"
      >
        {{ state.text || '-' }}
      </p>
      <div class="lyrics-controls" @mousedown.stop @click.stop>
        <button class="lyrics-control" type="button" title="上一首" @click="sendControl('prev')">
          <AppIcon name="skip-back" :size="15" />
        </button>
        <button class="lyrics-control primary" type="button" :title="state.isPlaying ? '暂停' : '播放'" @click="sendControl('toggle-play')">
          <AppIcon :name="state.isPlaying ? 'pause' : 'play'" :size="15" />
        </button>
        <button class="lyrics-control" type="button" title="下一首" @click="sendControl('next')">
          <AppIcon name="skip-forward" :size="15" />
        </button>
        <button class="lyrics-control text-control" type="button" title="减小字幕" @click="sendControl('font-smaller')">
          A-
        </button>
        <button class="lyrics-control text-control" type="button" title="放大字幕" @click="sendControl('font-larger')">
          A+
        </button>
        <button class="lyrics-control" type="button" :title="locked ? '解锁位置' : '锁定位置'" @click="toggleLocked">
          <AppIcon :name="locked ? 'lock' : 'unlock'" :size="15" />
        </button>
        <button class="lyrics-control danger" type="button" title="关闭悬浮歌词" @click="sendControl('close')">
          <AppIcon name="close" :size="15" />
        </button>
      </div>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import type { CSSProperties } from 'vue'
import AppIcon from '@/components/base/AppIcon.vue'
import { emitTo } from '@tauri-apps/api/event'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { isDesktopRuntime } from '@/desktop/runtime'

const LYRICS_UPDATE_EVENT = 'desktop-lyrics:update'
const LYRICS_READY_EVENT = 'desktop-lyrics:ready'
const LYRICS_CONTROL_EVENT = 'desktop-lyrics:control'
const LYRICS_LOCK_KEY = 'bili-radio:desktop-lyrics-locked'
const CONTROL_DEBOUNCE_MS = 240

type LyricsControlAction = 'toggle-play' | 'prev' | 'next' | 'font-smaller' | 'font-larger' | 'close'

interface LyricsPayload {
  enabled: boolean
  text: string
  color: string
  fontSize?: number
  title: string
  isPlaying?: boolean
}

const state = reactive<LyricsPayload>({
  enabled: false,
  text: '-',
  color: '#fb7299',
  fontSize: 30,
  title: '',
  isPlaying: false,
})
const locked = ref(loadLockedState())

let unlistenUpdate: (() => void) | null = null
let lastControlAction: LyricsControlAction | null = null
let lastControlAt = 0

const shellStyle = computed<CSSProperties>(
  () => {
    const fontSize = state.fontSize ?? 30
    const scale = fontSize / 30
    return ({
      '--lyrics-color': state.color,
      '--lyrics-font-size': `${fontSize}px`,
      '--lyrics-scale': String(scale),
      '--lyrics-shell-min-height': `${Math.round(66 * scale)}px`,
      '--lyrics-shell-padding-y': `${Math.round(14 * scale)}px`,
      '--lyrics-shell-padding-x': `${Math.round(28 * scale)}px`,
      '--lyrics-text-left-pad': `${Math.round(48 * scale)}px`,
      '--lyrics-text-right-pad': `${Math.round(188 * scale)}px`,
      '--lyrics-drag-size': `${Math.round(16 * scale)}px`,
    }) as CSSProperties
  }
)

onMounted(async () => {
  if (!isDesktopRuntime()) return
  const { emit, listen } = await import('@tauri-apps/api/event')
  unlistenUpdate = await listen<LyricsPayload>(LYRICS_UPDATE_EVENT, (event) => {
    applyLyricsPayload(event.payload)
  })
  await syncCurrentPayload()
  await emit(LYRICS_READY_EVENT)
})

onBeforeUnmount(() => {
  unlistenUpdate?.()
})

async function syncCurrentPayload() {
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    const payload = await invoke<LyricsPayload>('current_lyrics_window_payload')
    applyLyricsPayload(payload)
  } catch (error) {
    console.warn('Failed to sync desktop lyrics payload:', error)
  }
}

function applyLyricsPayload(payload: LyricsPayload) {
  state.enabled = payload.enabled
  state.text = payload.text || '-'
  state.color = payload.color || '#fb7299'
  state.fontSize = normalizeFontSize(payload.fontSize)
  state.title = payload.title || ''
  state.isPlaying = payload.isPlaying ?? false
}

async function startWindowDrag(event: MouseEvent) {
  if (!isDesktopRuntime() || locked.value || event.button !== 0) return
  event.preventDefault()
  try {
    await getCurrentWindow().startDragging()
  } catch (error) {
    console.warn('Failed to drag desktop lyrics window:', error)
  }
}

async function sendControl(action: LyricsControlAction) {
  if (!isDesktopRuntime()) return
  const now = performance.now()
  if (lastControlAction === action && now - lastControlAt < CONTROL_DEBOUNCE_MS) return
  lastControlAction = action
  lastControlAt = now
  try {
    await emitTo('main', LYRICS_CONTROL_EVENT, { action })
  } catch (error) {
    console.warn('Failed to send desktop lyrics control:', error)
  }
}

function toggleLocked() {
  locked.value = !locked.value
  localStorage.setItem(LYRICS_LOCK_KEY, locked.value ? '1' : '0')
}

function loadLockedState(): boolean {
  return localStorage.getItem(LYRICS_LOCK_KEY) === '1'
}

function normalizeFontSize(value: unknown): number {
  const size = Number(value)
  if (!Number.isFinite(size)) return 30
  return Math.max(22, Math.min(48, Math.round(size)))
}
</script>

<style scoped>
:global(html),
:global(body),
:global(#app) {
  width: 100%;
  height: 100%;
  margin: 0;
  background: transparent !important;
  overflow: hidden;
}

.lyrics-window {
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  pointer-events: auto;
  user-select: none;
}

.lyrics-shell {
  position: relative;
  width: calc(100vw - 20px);
  min-height: var(--lyrics-shell-min-height, 66px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--lyrics-shell-padding-y, 14px) var(--lyrics-shell-padding-x, 28px);
  border-radius: 8px;
  background: transparent;
  border: 1px solid transparent;
  box-shadow: none;
  pointer-events: auto;
  transition: border-color 120ms ease;
}

.lyrics-shell:hover {
  border-color: var(--lyrics-color, #fb7299);
}

.lyrics-controls {
  position: absolute;
  top: 8px;
  right: 10px;
  z-index: 3;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px;
  border-radius: 999px;
  background: rgba(10, 10, 14, 0.32);
  opacity: 0;
  pointer-events: none;
  transform: scale(var(--lyrics-scale, 1));
  transform-origin: top right;
  transition: opacity 120ms ease, background 120ms ease;
}

.lyrics-shell:hover .lyrics-controls,
.lyrics-controls:focus-within {
  opacity: 1;
  pointer-events: auto;
}

.lyrics-controls:hover {
  background: rgba(10, 10, 14, 0.48);
}

.lyrics-control {
  width: 26px;
  height: 26px;
  display: inline-grid;
  place-items: center;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  cursor: pointer;
  transition: background 120ms ease, border-color 120ms ease, color 120ms ease, transform 80ms ease;
}

.lyrics-control:hover {
  border-color: color-mix(in srgb, var(--lyrics-color, #fb7299) 72%, #fff 28%);
  background: rgba(255, 255, 255, 0.2);
  color: var(--lyrics-color, #fb7299);
}

.lyrics-control:active {
  transform: scale(0.94);
}

.lyrics-control.primary {
  border-color: color-mix(in srgb, var(--lyrics-color, #fb7299) 74%, #fff 26%);
  background: var(--lyrics-color, #fb7299);
  color: #fff;
}

.lyrics-control.danger:hover {
  border-color: rgba(255, 95, 125, 0.85);
  background: rgba(255, 95, 125, 0.24);
  color: #fff;
}

.lyrics-control.text-control {
  width: 30px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0;
}

.drag-border {
  position: absolute;
  pointer-events: auto;
  cursor: move;
}

.drag-border-top {
  top: -1px;
  left: -1px;
  right: -1px;
  height: var(--lyrics-drag-size, 16px);
}

.drag-border-right {
  top: -1px;
  right: -1px;
  bottom: -1px;
  width: var(--lyrics-drag-size, 16px);
}

.drag-border-bottom {
  right: -1px;
  bottom: -1px;
  left: -1px;
  height: var(--lyrics-drag-size, 16px);
}

.drag-border-left {
  top: -1px;
  bottom: -1px;
  left: -1px;
  width: var(--lyrics-drag-size, 16px);
}

.lyrics-text {
  width: 100%;
  margin: 0;
  padding: 0 var(--lyrics-text-right-pad, 188px) 0 var(--lyrics-text-left-pad, 48px);
  overflow: hidden;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--lyrics-font-size, 30px);
  font-weight: 700;
  line-height: 1.25;
  letter-spacing: 0;
  text-shadow:
    0 2px 1px rgba(0, 0, 0, 0.85),
    0 0 14px rgba(0, 0, 0, 0.42);
  pointer-events: none;
}
</style>
