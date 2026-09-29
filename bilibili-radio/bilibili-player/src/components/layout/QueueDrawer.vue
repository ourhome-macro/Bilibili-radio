<template>
  <Teleport to="body">
    <Transition name="drawer-fade">
      <div v-if="ui.queueOpen" class="drawer-mask" @click="ui.toggleQueue()" />
    </Transition>
    <Transition name="drawer-slide">
      <aside v-if="ui.queueOpen" class="queue-drawer">
        <header class="drawer-header">
          <div class="drawer-title">
            <span>播放队列</span>
            <span class="count">{{ player.queue.length }}</span>
          </div>
          <div class="drawer-actions">
            <button class="text-btn" title="定位当前播放" :disabled="!player.currentTrack" @click="locateCurrent(true)">定位</button>
            <button
              class="text-btn"
              :disabled="player.queue.length === 0"
              @click="player.clearQueue()"
            >
              清空
            </button>
            <button class="icon-btn" title="关闭" @click="ui.toggleQueue()">
              <AppIcon name="close" :size="18" />
            </button>
          </div>
        </header>

        <div ref="queueBody" class="drawer-body" @wheel.passive="followCurrent = false" @touchstart.passive="followCurrent = false" @pointerdown="followCurrent = false">
          <label v-if="player.queue.length > 0" class="local-search">
            <AppIcon name="search" :size="16" />
            <input v-model="query" type="search" placeholder="搜索播放队列" />
            <button v-if="query" type="button" title="清空搜索" @click="query = ''">
              <AppIcon name="close" :size="14" />
            </button>
          </label>

          <div v-if="filteredQueue.length > 0" class="queue-list">
            <div
              v-for="item in filteredQueue"
              :key="item.track.trackId ?? `${item.track.bvid}:${item.track.cid ?? item.queueIndex}`"
              class="queue-row"
              :data-queue-index="item.queueIndex"
              :data-track-id="playbackTrackId(item.track)"
              :class="{
                current: item.queueIndex === player.currentIndex,
                playing: player.isPlaying && item.queueIndex === player.currentIndex,
                dragging: dragIndex === item.queueIndex,
                'drop-target': dropIndex === item.queueIndex && dragIndex !== item.queueIndex,
              }"
            >
              <span
                class="drag-handle"
                title="拖动排序"
                role="button"
                tabindex="0"
                @pointerdown="startReorder(item.queueIndex, $event)"
              >
                ☰
              </span>
              <button class="queue-main" type="button" @click="player.playAt(item.queueIndex)">
                <span class="queue-index">{{ item.queueIndex + 1 }}</span>
                <span class="queue-title" :title="item.track.title">{{ item.track.title }}</span>
                <span v-if="item.track.duration" class="queue-duration">{{ formatTime(item.track.duration) }}</span>
              </button>
              <button
                class="row-icon"
                type="button"
                :title="library.isTrackLiked(item.track) ? '取消喜欢' : '喜欢'"
                :class="{ liked: library.isTrackLiked(item.track) }"
                @click="library.toggleLike(item.track)"
              >
                <AppIcon :name="library.isTrackLiked(item.track) ? 'heart-filled' : 'heart'" :size="16" />
              </button>
              <button class="row-icon" type="button" title="从队列移除" @click="player.removeFromQueue(item.queueIndex)">
                <AppIcon name="close" :size="16" />
              </button>
            </div>
          </div>

          <EmptyState
            v-else-if="player.queue.length === 0"
            title="队列还是空的"
            description="在搜索或收藏夹里双击一首，就会出现在这里"
          />
          <EmptyState
            v-else
            title="没有匹配的队列曲目"
            description="换个标题、UP 或 BV 号试试"
          />
        </div>
      </aside>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { usePlayerStore } from '@/stores/playerStore'
import { useLibraryStore } from '@/stores/libraryStore'
import { useUiStore } from '@/stores/uiStore'
import { usePointerReorder } from '@/composables/usePointerReorder'
import type { Track } from '@/types'
import AppIcon from '@/components/base/AppIcon.vue'
import EmptyState from '@/components/base/EmptyState.vue'
import { playbackTrackId } from '@/audio/playbackProgress'
import { locateTrack } from '@/composables/locateTrack'

const player = usePlayerStore()
const library = useLibraryStore()
const ui = useUiStore()
const query = ref('')
const queueBody = ref<HTMLElement | null>(null)
const followCurrent = ref(true)
const currentKey = computed(() => player.currentTrack ? playbackTrackId(player.currentTrack) : null)

async function locateCurrent(clearSearch = false) {
  if (clearSearch) query.value = ''
  followCurrent.value = true
  await nextTick()
  locateTrack(queueBody.value, currentKey.value)
}

watch([() => ui.queueOpen, currentKey, () => ui.queueLocateRequest], ([open, key, request], previous) => {
  if (!open) return
  if (!previous || request !== previous[2]) void locateCurrent(true)
  else if (!previous[0]) void locateCurrent()
  else if (key !== previous[1] && followCurrent.value && !query.value) void locateCurrent()
}, { flush: 'post' })
const { dragIndex, dropIndex, startReorder } = usePointerReorder({
  dataAttribute: 'data-queue-index',
  onMove: (from, to) => player.moveQueueItem(from, to),
})

const filteredQueue = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return player.queue
    .map((track, queueIndex) => ({ track, queueIndex }))
    .filter((item) => !keyword || matchesTrack(item.track, keyword))
})

function matchesTrack(track: Track, keyword: string): boolean {
  return [
    track.title,
    track.owner,
    track.bvid,
    track.pageTitle ?? '',
  ].some((value) => value.toLowerCase().includes(keyword))
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}
</script>

<style scoped>
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.28);
  z-index: 40;
}

.queue-drawer {
  position: fixed;
  top: 0;
  right: 0;
  width: var(--queue-width);
  height: calc(100vh - var(--player-height));
  background: var(--color-bg-content);
  border-left: 1px solid var(--color-border);
  box-shadow: var(--shadow-popup);
  z-index: 41;
  display: flex;
  flex-direction: column;
}

.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px 14px;
  border-bottom: 1px solid var(--color-border);
}

.drawer-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.count {
  font-size: 12px;
  color: var(--color-text-tertiary);
  font-weight: 400;
}

.drawer-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.text-btn {
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--radius-small);
  transition: background 160ms ease, color 160ms ease;
}

.text-btn:hover:not(:disabled) {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.text-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.icon-btn {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: 50%;
  cursor: pointer;
  transition: background 160ms ease;
}

.icon-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.local-search {
  height: 36px;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px 4px 8px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-app);
  color: var(--color-text-tertiary);
}

.local-search input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  outline: none;
  color: var(--color-text-primary);
  font-size: 13px;
}

.local-search button {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
}

.local-search button:hover {
  background: var(--color-bg-hover);
  color: var(--color-primary);
}

.queue-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.queue-row {
  min-height: 42px;
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) 30px 30px;
  align-items: center;
  gap: 2px;
  border-radius: var(--radius-small);
  color: var(--color-text-primary);
  transition: background 160ms ease, opacity 160ms ease;
}

.queue-row:hover,
.queue-row.current {
  background: var(--color-bg-hover);
}

.queue-row.playing .queue-title {
  color: var(--color-primary);
  font-weight: 600;
}

.queue-row.dragging {
  opacity: 0.55;
}

.queue-row.drop-target {
  background: var(--color-primary-soft);
}

.drag-handle {
  width: 26px;
  height: 42px;
  display: grid;
  place-items: center;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: grab;
  user-select: none;
  touch-action: none;
  font-size: 14px;
  line-height: 1;
}

.drag-handle:active {
  cursor: grabbing;
}

.queue-main {
  min-width: 0;
  height: 42px;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  border: none;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.queue-index,
.queue-duration {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.queue-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.row-icon {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
}

.row-icon:hover,
.row-icon.liked {
  background: var(--color-bg-hover);
  color: var(--color-primary);
}

/* 过渡 */
.drawer-fade-enter-active,
.drawer-fade-leave-active {
  transition: opacity 200ms ease;
}
.drawer-fade-enter-from,
.drawer-fade-leave-to {
  opacity: 0;
}

.drawer-slide-enter-active,
.drawer-slide-leave-active {
  transition: transform 240ms cubic-bezier(0.22, 1, 0.36, 1);
}
.drawer-slide-enter-from,
.drawer-slide-leave-to {
  transform: translateX(100%);
}

@media (prefers-reduced-motion: reduce) {
  .drawer-slide-enter-active,
  .drawer-slide-leave-active,
  .drawer-fade-enter-active,
  .drawer-fade-leave-active {
    transition: none;
  }
}
</style>
