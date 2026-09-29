<template>
  <div class="player-bar">
    <!-- 左侧 30%：当前内容 -->
    <div class="player-left">
      <template v-if="track">
        <div class="mini-cover" @click.stop="openNowPlaying">
          <img v-if="track.cover" :src="mediaUrl(track.cover)" :alt="track.title" />
          <div v-else class="cover-fallback">
            <AppIcon name="disc" :size="22" />
          </div>
          <div class="cover-mask">
            <AppIcon name="chevron" :size="18" class="expand-icon" />
          </div>
        </div>
        <div class="track-meta">
          <button class="meta-title meta-title-button" type="button" :title="track.title" @click.stop="openNowPlaying">
            {{ track.title }}
          </button>
          <button
            v-if="track.bvid"
            class="meta-owner owner-link"
            type="button"
            :title="`打开 UP 主页：${track.owner}`"
            @click.stop="openOwner"
          >
            {{ track.owner }}
          </button>
          <div v-else class="meta-owner" :title="track.owner">{{ track.owner }}</div>
        </div>
        <button
          class="icon-btn like-btn"
          :class="{ liked: isLiked }"
          :title="isLiked ? '取消喜欢' : '喜欢'"
          @click="toggleLike"
        >
          <AppIcon :name="isLiked ? 'heart-filled' : 'heart'" :size="18" />
        </button>
      </template>
      <div v-else class="empty-left">
        <div class="mini-cover placeholder">
          <AppIcon name="disc" :size="22" />
        </div>
        <div class="track-meta">
          <div class="meta-title muted">未在播放</div>
          <div class="meta-owner">选一首开始吧</div>
        </div>
      </div>
    </div>

    <!-- 中间 40%：核心控制 -->
    <div class="player-center">
      <div class="control-row">
        <button
          class="icon-btn mode-btn"
          :title="modeLabel"
          @click="player.cyclePlayMode()"
        >
          <AppIcon :name="modeIcon" :size="18" />
        </button>
        <button class="icon-btn" title="上一首" :disabled="!hasQueue" @click="player.prev()">
          <AppIcon name="skip-back" :size="20" />
        </button>
        <button
          class="play-btn"
          :disabled="!canPlay"
          :title="isPlaying ? '暂停' : '播放'"
          @click="player.togglePlayPause()"
        >
          <LoadingDots v-if="isLoading" light />
          <AppIcon v-else :name="isPlaying ? 'pause' : 'play'" :size="22" />
        </button>
        <button class="icon-btn" title="下一首" :disabled="!hasQueue" @click="player.next()">
          <AppIcon name="skip-forward" :size="20" />
        </button>
        <button
          ref="queueButtonRef"
          class="icon-btn queue-btn"
          title="播放队列"
          :class="{ active: ui.queueOpen }"
          @click="ui.toggleQueue()"
        >
          <AppIcon name="queue" :size="18" />
          <span v-if="hasQueue" class="queue-count">{{ player.queue.length }}</span>
        </button>
      </div>

      <div class="progress-row">
        <span class="time">{{ player.formattedCurrentTime }}</span>
        <ProgressBar />
        <span class="time">{{ player.formattedDuration }}</span>
      </div>
      <span v-if="player.progressSyncError" class="progress-sync-note" role="status">{{ player.progressSyncError }}</span>
    </div>

    <!-- 右侧 30%：辅助操作 -->
    <div class="player-right">
      <VolumeControl />
      <select
        class="setting-select quality-select"
        :value="player.audioQualityPreference"
        title="音频流 / 音质"
        @change="changeAudioQuality"
      >
        <option v-for="quality in audioQualityOptions" :key="quality.value" :value="quality.value">
          {{ quality.label }}
        </option>
      </select>
      <select class="setting-select speed-select" :value="player.playbackSpeed" title="倍速" @change="changePlaybackSpeed">
        <option v-for="speed in playbackSpeedOptions" :key="speed.value" :value="speed.value">
          {{ speed.label }}
        </option>
      </select>
      <button
        class="lyrics-toggle-btn"
        :class="{ active: ui.lyricsOverlayEnabled }"
        :title="ui.lyricsOverlayEnabled ? '关闭桌面歌词' : '打开桌面歌词'"
        :disabled="!isDesktop"
        @click="toggleDesktopLyrics"
      >
        <AppIcon name="subtitle" :size="18" />
        <span class="lyrics-toggle-text">
          {{ ui.lyricsOverlayEnabled ? '关闭歌词' : '桌面歌词' }}
        </span>
      </button>
      <div v-if="isDesktop && ui.lyricsOverlayEnabled" class="lyrics-colors" aria-label="桌面歌词颜色">
        <button
          v-for="color in ui.lyricsOverlayColors"
          :key="color"
          class="lyrics-color"
          :class="{ selected: color === ui.lyricsOverlayColor }"
          :style="{ backgroundColor: color }"
          :title="`桌面歌词颜色 ${color}`"
          @click="setDesktopLyricsColor(color)"
        />
      </div>
      <div v-if="isDesktop && ui.lyricsOverlayEnabled" class="lyrics-font-size" aria-label="桌面歌词字号">
        <button class="lyrics-size-btn" type="button" title="减小字幕" @click="changeDesktopLyricsFontSize(-2)">A-</button>
        <span>{{ ui.lyricsOverlayFontSize }}</span>
        <button class="lyrics-size-btn" type="button" title="放大字幕" @click="changeDesktopLyricsFontSize(2)">A+</button>
      </div>
      <button class="icon-btn pip-btn" title="画中画（暂未接入）" disabled>
        <AppIcon name="pip" :size="18" />
      </button>
      <button
        class="icon-btn"
        :title="isDownloading ? '下载中…' : '下载当前音频'"
        :disabled="!track || isDownloading"
        @click="player.downloadCurrent()"
      >
        <AppIcon name="download" :size="18" :class="{ 'spin-slow': isDownloading }" />
      </button>
      <button class="icon-btn" title="打开播放详情" :disabled="!canOpenNowPlaying" @click.stop="openNowPlaying">
        <AppIcon name="fullscreen" :size="18" />
      </button>
    </div>
    <span
      v-for="effect in queueAddEffects"
      :key="effect.id"
      class="queue-add-effect"
      :style="{
        left: `${effect.x}px`,
        top: `${effect.y}px`,
        '--queue-effect-dx': `${effect.dx}px`,
        '--queue-effect-dy': `${effect.dy}px`,
      }"
    >+</span>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { usePlayerStore } from '@/stores/playerStore'
import { useLibraryStore } from '@/stores/libraryStore'
import { useUiStore } from '@/stores/uiStore'
import { mediaUrl } from '@/api/client'
import { useOpenOwner } from '@/composables/useOpenOwner'
import type { AudioQualityPreference, PlayMode, Track } from '@/types'
import AppIcon from '@/components/base/AppIcon.vue'
import LoadingDots from '@/components/base/LoadingDots.vue'
import ProgressBar from '@/components/ProgressBar.vue'
import VolumeControl from '@/components/VolumeControl.vue'

const player = usePlayerStore()
const library = useLibraryStore()
const ui = useUiStore()
const { openTrackOwner } = useOpenOwner()

const { currentTrack, videoInfo, isPlaying, isLoading, isDownloading } =
  storeToRefs(player)
const queueButtonRef = ref<HTMLElement | null>(null)
const queueAddEffects = ref<Array<{ id: number; x: number; y: number; dx: number; dy: number }>>([])
let queueEffectId = 0

// 当前展示的曲目：优先队列曲目，否则回退到裸 videoInfo（直接输入播放的场景）
const track = computed<Track | null>(() => {
  if (currentTrack.value) return currentTrack.value
  if (videoInfo.value) {
    return {
      trackId: videoInfo.value.trackId,
      bvid: videoInfo.value.bvid,
      cid: videoInfo.value.cid,
      title: videoInfo.value.title,
      owner: videoInfo.value.owner,
      ownerMid: videoInfo.value.ownerMid,
      cover: videoInfo.value.cover,
      duration: videoInfo.value.duration,
      playCount: videoInfo.value.playCount,
      publishedAt: videoInfo.value.publishedAt,
    }
  }
  return null
})

const hasQueue = computed(() => player.queue.length > 0)
const canPlay = computed(() => track.value !== null && !isLoading.value)
const canOpenNowPlaying = computed(() => player.hasTrack)
const isLiked = computed(() => (track.value ? library.isLiked(track.value.bvid) : false))
const isDesktop = computed(() => window.location.protocol === 'tauri:' || window.location.hostname === 'tauri.localhost')

const MODE_META: Record<PlayMode, { icon: string; label: string }> = {
  order: { icon: 'repeat', label: '顺序播放' },
  loop: { icon: 'repeat', label: '列表循环' },
  single: { icon: 'repeat-one', label: '单曲循环' },
  shuffle: { icon: 'shuffle', label: '随机播放' },
}
const modeIcon = computed(() => MODE_META[player.playMode].icon)
const modeLabel = computed(() => MODE_META[player.playMode].label)

const BASE_AUDIO_QUALITY_OPTIONS: Array<{ value: AudioQualityPreference; label: string; dynamic?: boolean }> = [
  { value: 'auto', label: '自动' },
  { value: '64k', label: '64K' },
  { value: '132k', label: '132K' },
  { value: '192k', label: '192K' },
  { value: 'dolby', label: 'Dolby', dynamic: true },
  { value: 'hires', label: 'Hi-Res', dynamic: true },
]

const playbackSpeedOptions = [
  { value: 0.5, label: '0.5x' },
  { value: 0.75, label: '0.75x' },
  { value: 1, label: '1x' },
  { value: 1.25, label: '1.25x' },
  { value: 1.5, label: '1.5x' },
  { value: 2, label: '2x' },
]

const audioQualityOptions = computed(() => {
  const available = new Set(player.availableAudioQualities)
  available.add('auto')
  available.add('64k')
  available.add('132k')
  available.add('192k')
  available.add(player.audioQualityPreference)
  return BASE_AUDIO_QUALITY_OPTIONS.filter((option) => !option.dynamic || available.has(option.value))
})

onMounted(() => {
  window.addEventListener('bili-radio:queue-add-effect', handleQueueAddEffect)
})

onBeforeUnmount(() => {
  window.removeEventListener('bili-radio:queue-add-effect', handleQueueAddEffect)
})

function toggleLike() {
  if (track.value) library.toggleLike(track.value)
}

function openNowPlaying() {
  if (!player.hasTrack) return
  ui.openNowPlaying()
}

function openOwner() {
  void openTrackOwner(track.value).then((opened) => {
    if (!opened) {
      player.statusMessage = '无法打开 UP 主页：缺少 UP 主 ID'
    }
  }).catch((error) => {
    player.statusMessage = error instanceof Error ? error.message : '无法打开 UP 主页'
  })
}

function changeAudioQuality(event: Event) {
  const value = (event.target as HTMLSelectElement).value as AudioQualityPreference
  player.setAudioQualityPreference(value)
}

function changePlaybackSpeed(event: Event) {
  player.setPlaybackSpeed(Number((event.target as HTMLSelectElement).value))
}

async function toggleDesktopLyrics() {
  if (!isDesktop.value) return
  const enabled = !ui.lyricsOverlayEnabled
  console.info('[desktop-lyrics] player bar toggle click', { enabled })
  ui.setLyricsOverlayEnabled(enabled)
  if (enabled) {
    void player.loadCurrentSubtitles()
  }
}

function setDesktopLyricsColor(color: string) {
  ui.setLyricsOverlayColor(color)
}

function changeDesktopLyricsFontSize(delta: number) {
  ui.setLyricsOverlayFontSize(ui.lyricsOverlayFontSize + delta)
}

function handleQueueAddEffect(event: Event) {
  const detail = (event as CustomEvent<{ x?: number; y?: number }>).detail
  const buttonRect = queueButtonRef.value?.getBoundingClientRect()
  if (!buttonRect || typeof detail?.x !== 'number' || typeof detail?.y !== 'number') return
  const targetX = buttonRect.left + buttonRect.width / 2
  const targetY = buttonRect.top + buttonRect.height / 2
  const effect = {
    id: ++queueEffectId,
    x: detail.x,
    y: detail.y,
    dx: targetX - detail.x,
    dy: targetY - detail.y,
  }
  queueAddEffects.value.push(effect)
  window.setTimeout(() => {
    queueAddEffects.value = queueAddEffects.value.filter((item) => item.id !== effect.id)
  }, 720)
}
</script>

<style scoped>
.progress-sync-note { font-size: 11px; color: var(--color-text-secondary); position: absolute; bottom: 2px; }
.player-bar {
  height: var(--player-height);
  background: var(--color-bg-content);
  border-top: 1px solid var(--color-border);
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(340px, 1.25fr) minmax(320px, max-content);
  align-items: center;
  padding: 0 20px;
  gap: 16px;
}

/* 左侧 */
.player-left,
.empty-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.mini-cover {
  position: relative;
  width: 48px;
  height: 48px;
  border-radius: var(--radius-small);
  overflow: hidden;
  flex-shrink: 0;
  cursor: pointer;
  background: var(--color-bg-hover);
}

.mini-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-fallback,
.mini-cover.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: var(--color-text-tertiary);
}

.mini-cover.placeholder {
  cursor: default;
}

.cover-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  color: #fff;
  opacity: 0;
  transition: opacity 160ms ease;
}

.mini-cover:hover .cover-mask {
  opacity: 1;
}

.expand-icon {
  transform: rotate(180deg);
}

.track-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.meta-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.meta-title-button {
  min-width: 0;
  border: none;
  background: transparent;
  padding: 0;
  text-align: left;
  cursor: pointer;
  transition: color 160ms ease;
}

.meta-title-button:hover {
  color: var(--color-primary);
}

.meta-title.muted {
  color: var(--color-text-secondary);
  font-weight: 400;
}

.meta-owner {
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.owner-link {
  min-width: 0;
  border: none;
  background: transparent;
  padding: 0;
  text-align: left;
  cursor: pointer;
  transition: color 160ms ease;
}

.owner-link:hover {
  color: var(--color-primary);
}

/* 中间 */
.player-center {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.control-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.play-btn {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  border: none;
  background: var(--color-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 160ms ease, transform 120ms ease;
  box-shadow: 0 2px 8px rgba(251, 114, 153, 0.35);
}

.play-btn:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.play-btn:active:not(:disabled) {
  transform: scale(0.94);
}

.play-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  box-shadow: none;
}

.progress-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  max-width: 520px;
}

.time {
  font-size: 11px;
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
  min-width: 38px;
  text-align: center;
}

/* 右侧 */
.player-right {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  min-width: 0;
}

.setting-select {
  height: 30px;
  min-width: 68px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-content);
  color: var(--color-text-secondary);
  font-size: 12px;
  outline: none;
  cursor: pointer;
}

.setting-select:hover,
.setting-select:focus {
  border-color: var(--color-primary);
  color: var(--color-text-primary);
}

.speed-select {
  min-width: 58px;
}

/* 通用图标按钮 */
.icon-btn {
  position: relative;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: 50%;
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease;
}

.icon-btn:hover:not(:disabled) {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.icon-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.icon-btn.active,
.mode-btn.active,
.lyrics-toggle-btn.active {
  color: var(--color-primary);
}

.like-btn.liked {
  color: var(--color-primary);
}

.queue-btn.active {
  color: var(--color-primary);
}

.lyrics-toggle-btn {
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 10px;
  border: none;
  border-radius: 999px;
  background: var(--color-bg-hover);
  color: var(--color-text-secondary);
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease;
}

.lyrics-toggle-btn:hover:not(:disabled) {
  background: var(--color-primary-soft);
  color: var(--color-primary);
}

.lyrics-toggle-btn:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

.lyrics-toggle-text {
  line-height: 1;
}

.lyrics-colors {
  height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 7px;
  border-radius: 999px;
  background: var(--color-bg-hover);
}

.lyrics-font-size {
  height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 7px;
  border-radius: 999px;
  background: var(--color-bg-hover);
  color: var(--color-text-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.lyrics-size-btn {
  min-width: 26px;
  height: 24px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 11px;
  cursor: pointer;
}

.lyrics-size-btn:hover {
  background: var(--color-primary-soft);
  color: var(--color-primary);
}

.lyrics-color {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.52);
  border-radius: 50%;
  cursor: pointer;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.14);
}

.lyrics-color.selected {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 2px rgba(251, 114, 153, 0.24);
}

.queue-count {
  position: absolute;
  top: 0;
  right: 0;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  border-radius: 8px;
  background: var(--color-primary);
  color: #fff;
  font-size: 10px;
  line-height: 15px;
  text-align: center;
}

.queue-add-effect {
  position: fixed;
  z-index: 10000;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  margin: -12px 0 0 -12px;
  border-radius: 50%;
  background: var(--color-primary);
  color: #fff;
  font-size: 18px;
  font-weight: 700;
  line-height: 1;
  pointer-events: none;
  box-shadow: 0 8px 22px rgba(251, 114, 153, 0.34);
  animation: queue-add-fly 680ms cubic-bezier(0.2, 0.8, 0.2, 1) forwards;
}

.spin-slow {
  animation: spin-slow 1.2s linear infinite;
}

@keyframes queue-add-fly {
  0% {
    opacity: 0;
    transform: translate3d(0, 0, 0) scale(0.7);
  }
  18% {
    opacity: 1;
    transform: translate3d(0, -10px, 0) scale(1);
  }
  100% {
    opacity: 0;
    transform: translate3d(var(--queue-effect-dx), var(--queue-effect-dy), 0) scale(0.42);
  }
}

@keyframes spin-slow {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .spin-slow,
  .queue-add-effect {
    animation: none;
  }
}

@media (max-width: 1180px) {
  .player-bar {
    grid-template-columns: minmax(180px, 0.9fr) minmax(300px, 1.35fr) minmax(250px, auto);
    padding: 0 12px;
    gap: 10px;
  }

  .player-left,
  .empty-left {
    gap: 8px;
  }

  .mini-cover {
    width: 42px;
    height: 42px;
  }

  .control-row {
    gap: 8px;
  }

  .icon-btn {
    width: 30px;
    height: 30px;
  }

  .play-btn {
    width: 38px;
    height: 38px;
  }

  .player-right {
    gap: 4px;
  }

  .lyrics-toggle-btn {
    width: 34px;
    padding: 0;
    border-radius: 50%;
  }

  .lyrics-toggle-text,
  .lyrics-colors,
  .lyrics-font-size {
    display: none;
  }
}

@media (max-width: 980px) {
  .player-bar {
    grid-template-columns: minmax(150px, 0.75fr) minmax(280px, 1.4fr) auto;
  }

  .like-btn,
  .pip-btn {
    display: none;
  }

  .setting-select {
    min-width: 58px;
    max-width: 64px;
  }

  .quality-select {
    max-width: 72px;
  }
}

@media (max-width: 860px) {
  .player-bar {
    grid-template-columns: minmax(0, 1fr) minmax(260px, 1.3fr);
  }

  .player-right {
    display: none;
  }
}

@media (max-width: 720px) {
  .player-bar {
    grid-template-columns: minmax(0, 1fr);
    padding: 0 10px;
    gap: 0;
  }

  .player-left,
  .player-right {
    display: none;
  }

  .player-center {
    min-width: 0;
  }

  .control-row {
    gap: 10px;
  }

  .progress-row {
    max-width: 390px;
  }
}
</style>
