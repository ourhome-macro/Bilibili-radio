import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

type Theme = 'light' | 'dark'

const THEME_KEY = 'bili-radio:theme'
const LYRICS_OVERLAY_COLOR_KEY = 'bili-radio:lyrics-overlay-color'
const LYRICS_OVERLAY_FONT_SIZE_KEY = 'bili-radio:lyrics-overlay-font-size'
const DEFAULT_LYRICS_OVERLAY_COLOR = '#fb7299'
const DEFAULT_LYRICS_OVERLAY_FONT_SIZE = 30
const MIN_LYRICS_OVERLAY_FONT_SIZE = 22
const MAX_LYRICS_OVERLAY_FONT_SIZE = 48
const LYRICS_OVERLAY_COLORS = ['#fb7299', '#ffffff', '#ffd166', '#66e3ff', '#8ef08e', '#c7a6ff']

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme)
}

function detectReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function clampLyricsFontSize(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_LYRICS_OVERLAY_FONT_SIZE
  return Math.max(MIN_LYRICS_OVERLAY_FONT_SIZE, Math.min(MAX_LYRICS_OVERLAY_FONT_SIZE, Math.round(value)))
}

export const useUiStore = defineStore('ui', () => {
  const stored = (localStorage.getItem(THEME_KEY) as Theme | null) ?? 'light'
  const theme = ref<Theme>(stored)
  const queueOpen = ref(false)
  const queueLocateRequest = ref(0)
  const nowPlayingOpen = ref(false)
  const reducedMotion = ref(detectReducedMotion())
  const lyricsOverlayEnabled = ref(false)
  const storedLyricsColor = localStorage.getItem(LYRICS_OVERLAY_COLOR_KEY)
  const lyricsOverlayColor = ref(
    storedLyricsColor && LYRICS_OVERLAY_COLORS.includes(storedLyricsColor)
      ? storedLyricsColor
      : DEFAULT_LYRICS_OVERLAY_COLOR
  )
  const lyricsOverlayFontSize = ref(
    clampLyricsFontSize(Number(localStorage.getItem(LYRICS_OVERLAY_FONT_SIZE_KEY) ?? DEFAULT_LYRICS_OVERLAY_FONT_SIZE))
  )

  applyTheme(theme.value)

  watch(theme, (value) => {
    applyTheme(value)
    localStorage.setItem(THEME_KEY, value)
  })

  watch(lyricsOverlayColor, (value) => {
    localStorage.setItem(LYRICS_OVERLAY_COLOR_KEY, value)
  })

  watch(lyricsOverlayFontSize, (value) => {
    localStorage.setItem(LYRICS_OVERLAY_FONT_SIZE_KEY, String(value))
  })

  function toggleTheme() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
  }

  function toggleQueue() {
    queueOpen.value = !queueOpen.value
  }

  function locateCurrentInQueue() {
    queueOpen.value = true
    queueLocateRequest.value++
  }

  function openNowPlaying() {
    nowPlayingOpen.value = true
  }

  function closeNowPlaying() {
    nowPlayingOpen.value = false
  }

  function toggleLyricsOverlay() {
    lyricsOverlayEnabled.value = !lyricsOverlayEnabled.value
  }

  function setLyricsOverlayEnabled(value: boolean) {
    lyricsOverlayEnabled.value = value
  }

  function setLyricsOverlayColor(value: string) {
    if (LYRICS_OVERLAY_COLORS.includes(value)) {
      lyricsOverlayColor.value = value
    }
  }

  function setLyricsOverlayFontSize(value: number) {
    lyricsOverlayFontSize.value = clampLyricsFontSize(value)
  }

  function increaseLyricsOverlayFontSize() {
    setLyricsOverlayFontSize(lyricsOverlayFontSize.value + 2)
  }

  function decreaseLyricsOverlayFontSize() {
    setLyricsOverlayFontSize(lyricsOverlayFontSize.value - 2)
  }

  return {
    theme,
    queueOpen,
    queueLocateRequest,
    locateCurrentInQueue,
    nowPlayingOpen,
    reducedMotion,
    lyricsOverlayEnabled,
    lyricsOverlayColor,
    lyricsOverlayFontSize,
    lyricsOverlayColors: LYRICS_OVERLAY_COLORS,
    toggleTheme,
    toggleQueue,
    openNowPlaying,
    closeNowPlaying,
    toggleLyricsOverlay,
    setLyricsOverlayEnabled,
    setLyricsOverlayColor,
    setLyricsOverlayFontSize,
    increaseLyricsOverlayFontSize,
    decreaseLyricsOverlayFontSize,
  }
})
