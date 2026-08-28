<template>
  <span class="desktop-lyrics-bridge" aria-hidden="true" />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { isDesktopRuntime } from '@/desktop/runtime'
import { usePlayerStore } from '@/stores/playerStore'
import { useUiStore } from '@/stores/uiStore'

const LYRICS_READY_EVENT = 'desktop-lyrics:ready'
const LYRICS_CONTROL_EVENT = 'desktop-lyrics:control'

type LyricsControlAction = 'toggle-play' | 'prev' | 'next' | 'font-smaller' | 'font-larger' | 'close'

interface LyricsPayload {
  enabled: boolean
  text: string
  color: string
  fontSize: number
  title: string
  isPlaying: boolean
}

interface LyricsControlPayload {
  action: LyricsControlAction
}

interface LyricsWindowDebug {
  action: string
  requested_enabled?: boolean | null
  status_before?: unknown
  status_after_show?: unknown
  status_after?: unknown
  steps?: unknown[]
}

const player = usePlayerStore()
const ui = useUiStore()
const { currentTrack, videoInfo, desktopLyricText, isPlaying, playRequestSerial } = storeToRefs(player)

let unlistenReady: (() => void) | null = null
let unlistenControl: (() => void) | null = null
let lastPayloadKey = ''
let publishRetryTimers: number[] = []
let bindPromise: Promise<void> | null = null
let showPromise: Promise<void> | null = null

const trackKey = computed(() => {
  const track = currentTrack.value
  const info = videoInfo.value
  return `${track?.bvid ?? info?.bvid ?? ''}:${track?.cid ?? info?.cid ?? ''}`
})

const trackTitle = computed(() => currentTrack.value?.title ?? videoInfo.value?.title ?? '')

onMounted(() => {
  if (!isDesktopRuntime()) return
  void bindLyricsWindowEvents()
})

watch(
  () => ui.lyricsOverlayEnabled,
  (enabled, previousEnabled) => {
    if (enabled) {
      void player.loadCurrentSubtitles()
      void showLyricsWindow().then(() => publishLyricsStateWithRetries())
    } else if (previousEnabled === true) {
      void hideLyricsWindow()
    }
  },
  { immediate: true }
)

watch(
  [trackKey, playRequestSerial],
  () => {
    if (!ui.lyricsOverlayEnabled) return
    void player.loadCurrentSubtitles(true).finally(() => {
      void publishLyricsState(true)
    })
  }
)

watch(
  [desktopLyricText, () => ui.lyricsOverlayColor, () => ui.lyricsOverlayFontSize, trackTitle, isPlaying],
  () => {
    if (!ui.lyricsOverlayEnabled) return
    void publishLyricsState()
  }
)

onBeforeUnmount(() => {
  clearPublishRetryTimers()
  void hideLyricsWindow()
  unlistenReady?.()
  unlistenControl?.()
})

async function showLyricsWindow() {
  if (!isDesktopRuntime() || !ui.lyricsOverlayEnabled) return
  if (showPromise) return showPromise
  const payload = currentLyricsPayload()
  showPromise = (async () => {
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      const debug = await invoke<LyricsWindowDebug>('set_lyrics_window_payload', {
        enabled: payload.enabled,
        text: payload.text,
        color: payload.color,
        fontSize: payload.fontSize,
        title: payload.title,
        isPlaying: payload.isPlaying,
      })
      console.info('[desktop-lyrics] bridge show', debug)
    } catch (error) {
      console.warn('Failed to show desktop lyrics window:', error)
    } finally {
      showPromise = null
    }
  })()
  return showPromise
}

async function hideLyricsWindow() {
  if (!isDesktopRuntime()) return
  clearPublishRetryTimers()
  lastPayloadKey = ''
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    const debug = await invoke<LyricsWindowDebug>('hide_lyrics_window')
    console.info('[desktop-lyrics] bridge hide', debug)
  } catch (error) {
    console.warn('Failed to hide desktop lyrics window:', error)
  }
}

async function publishLyricsState(force = false) {
  if (!isDesktopRuntime() || !ui.lyricsOverlayEnabled) return
  const payload = currentLyricsPayload()
  const payloadKey = JSON.stringify(payload)
  if (!force && payloadKey === lastPayloadKey) return
  lastPayloadKey = payloadKey
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    const debug = await invoke<LyricsWindowDebug>('set_lyrics_window_payload', {
      enabled: payload.enabled,
      text: payload.text,
      color: payload.color,
      fontSize: payload.fontSize,
      title: payload.title,
      isPlaying: payload.isPlaying,
    })
    console.info('[desktop-lyrics] bridge payload', debug)
  } catch (error) {
    console.warn('Failed to update desktop lyrics window:', error)
  }
}

function publishLyricsStateWithRetries() {
  clearPublishRetryTimers()
  void publishLyricsState(true)
  for (const delay of [120, 360, 900]) {
    const timer = window.setTimeout(() => {
      void publishLyricsState(true)
    }, delay)
    publishRetryTimers.push(timer)
  }
}

function clearPublishRetryTimers() {
  for (const timer of publishRetryTimers) {
    window.clearTimeout(timer)
  }
  publishRetryTimers = []
}

function currentLyricsPayload(): LyricsPayload {
  return {
    enabled: ui.lyricsOverlayEnabled,
    text: desktopLyricText.value || '-',
    color: ui.lyricsOverlayColor,
    fontSize: ui.lyricsOverlayFontSize,
    title: trackTitle.value,
    isPlaying: isPlaying.value,
  }
}

async function bindLyricsWindowEvents() {
  if (unlistenReady && unlistenControl) return
  if (bindPromise) return bindPromise
  bindPromise = (async () => {
    const { listen } = await import('@tauri-apps/api/event')
    if (!unlistenReady) {
      unlistenReady = await listen(LYRICS_READY_EVENT, () => {
        void publishLyricsState(true)
      })
    }
    if (!unlistenControl) {
      unlistenControl = await listen<LyricsControlPayload>(LYRICS_CONTROL_EVENT, (event) => {
        handleLyricsControl(event.payload)
      })
    }
  })().finally(() => {
    bindPromise = null
  })
  return bindPromise
}

function handleLyricsControl(payload: LyricsControlPayload) {
  switch (payload.action) {
    case 'toggle-play':
      if (player.status === 'playing') {
        player.pause()
      } else if (player.status === 'paused') {
        player.resume()
      }
      break
    case 'prev':
      player.prev()
      break
    case 'next':
      player.next()
      break
    case 'font-smaller':
      ui.decreaseLyricsOverlayFontSize()
      break
    case 'font-larger':
      ui.increaseLyricsOverlayFontSize()
      break
    case 'close':
      ui.setLyricsOverlayEnabled(false)
      void hideLyricsWindow()
      break
  }
}

</script>

<style scoped>
.desktop-lyrics-bridge {
  display: none;
}
</style>
