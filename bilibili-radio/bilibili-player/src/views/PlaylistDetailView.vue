<template>
  <div ref="pageRoot" class="page" @wheel.passive="followCurrent = false" @touchstart.passive="followCurrent = false">
    <template v-if="playlist">
      <div class="detail-top">
        <div class="detail-cover">
          <img v-if="coverPreview" :src="mediaUrl(coverPreview)" :alt="draftName" />
          <div v-else class="cover-fallback">
            <AppIcon name="list" :size="40" />
          </div>
        </div>
        <div class="detail-info">
          <span class="detail-kind">{{ collectionKind }}</span>
          <input v-model="draftName" class="title-input" maxlength="64" />
          <p class="detail-meta">{{ playlist.tracks.length }} 首</p>
          <div class="detail-actions">
            <button class="ghost-btn" :disabled="!containsCurrent" :title="containsCurrent ? '定位当前播放' : '当前曲目不在此歌单'" @click="locateCurrent">定位当前播放</button>
            <button class="primary-btn" :disabled="!playlist.tracks.length" @click="playAll">
              <AppIcon name="play" :size="16" />
              <span>播放全部</span>
            </button>
            <button class="ghost-btn" :disabled="!canSaveMeta" @click="saveMeta">
              <AppIcon name="list" :size="16" />
              <span>保存信息</span>
            </button>
            <button class="ghost-btn danger" @click="remove">
              <AppIcon name="trash" :size="16" />
              <span>删除歌单</span>
            </button>
          </div>
        </div>
      </div>

      <form class="add-track-form" @submit.prevent="addInputTracks">
        <AppIcon name="plus" :size="16" />
        <input
          v-model="addInput"
          :disabled="addLoading"
          placeholder="添加 BV 号或 B 站视频链接"
          autocomplete="off"
        />
        <button class="primary-btn" type="submit" :disabled="!addInput.trim() || addLoading">
          <LoadingDots v-if="addLoading" light />
          <span v-else>添加</span>
        </button>
      </form>
      <p v-if="message" class="page-message" :class="{ error: messageKind === 'error' }">{{ message }}</p>

      <div v-if="playlist.tracks.length" class="result-list">
        <div
          v-for="(t, i) in playlist.tracks"
          :key="t.trackId ?? `${t.bvid}:${t.cid ?? i}`"
          class="draggable-row"
          :data-track-index="i"
          :data-track-id="playbackTrackId(t)"
          :class="{ dragging: dragIndex === i, 'drop-target': dropIndex === i && dragIndex !== i }"
        >
          <span
            class="drag-handle"
            title="拖拽排序"
            role="button"
            tabindex="0"
            @pointerdown="startReorder(i, $event)"
          >
            ☰
          </span>
          <TrackRow
            :track="t"
            :index="i"
            :is-current="isCurrent(t)"
            :is-playing="isPlaying && isCurrent(t)"
            :is-liked="library.isTrackLiked(t)"
            removable
            @play="player.playList(playlist.tracks, i)"
            @like="library.toggleLike(t)"
            @enqueue="player.enqueue(t)"
            @remove="removeTrack(i)"
          />
        </div>
      </div>
      <EmptyState
        v-else
        title="这个集合还是空的"
        description="粘贴 BV 号或视频链接，可以一次导入整组多 P"
      />
    </template>

    <EmptyState v-else title="歌单不存在" description="它可能已被删除">
      <RouterLink to="/" class="empty-link">回到首页</RouterLink>
    </EmptyState>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { storeToRefs } from 'pinia'
import { resolveTrackInput, mediaUrl } from '@/api/client'
import { usePlayerStore } from '@/stores/playerStore'
import { useLibraryStore } from '@/stores/libraryStore'
import { usePointerReorder } from '@/composables/usePointerReorder'
import type { Track } from '@/types'
import AppIcon from '@/components/base/AppIcon.vue'
import EmptyState from '@/components/base/EmptyState.vue'
import LoadingDots from '@/components/base/LoadingDots.vue'
import TrackRow from '@/components/TrackRow.vue'
import { playbackTrackId } from '@/audio/playbackProgress'
import { locateTrack } from '@/composables/locateTrack'

const route = useRoute()
const router = useRouter()
const player = usePlayerStore()
const library = useLibraryStore()
const { currentTrack, isPlaying } = storeToRefs(player)

const playlist = computed(() => library.getPlaylist(route.params.id as string))
const pageRoot = ref<HTMLElement | null>(null)
const followCurrent = ref(true)
const containsCurrent = computed(() => playlist.value?.tracks.some(isCurrent) ?? false)
const currentKey = computed(() => currentTrack.value ? playbackTrackId(currentTrack.value) : null)
async function locateCurrent() {
  followCurrent.value = true
  await nextTick()
  locateTrack(pageRoot.value, currentKey.value)
}
watch([() => playlist.value?.id, currentKey], ([id], previous) => {
  if (!previous || id !== previous[0] || followCurrent.value) void locateCurrent()
}, { immediate: true, flush: 'post' })
const draftName = ref('')
const addInput = ref('')
const addLoading = ref(false)
const message = ref('')
const messageKind = ref<'info' | 'error'>('info')
const { dragIndex, dropIndex, startReorder, cancelReorder } = usePointerReorder({
  dataAttribute: 'data-track-index',
  onMove: moveTrack,
})

const coverPreview = computed(() => playlist.value?.cover || playlist.value?.tracks[0]?.cover || '')
const collectionKind = computed(() => {
  const sourceType = playlist.value?.sourceType
  if (sourceType === 'bilibili-multipage') return 'B 站多 P 集合'
  if (sourceType === 'bilibili-favorite') return 'B 站收藏夹集合'
  return '自定义曲目集合'
})
const canSaveMeta = computed(() => {
  const current = playlist.value
  if (!current) return false
  const name = draftName.value.trim()
  return !!name && name !== current.name
})

watch(
  playlist,
  (current) => {
    draftName.value = current?.name ?? ''
    message.value = ''
    cancelReorder()
  },
  { immediate: true }
)

function isCurrent(track: Track): boolean {
  const current = currentTrack.value
  if (!current) return false
  if (current.trackId && track.trackId) return current.trackId === track.trackId
  if (current.cid != null && track.cid != null) return current.bvid === track.bvid && current.cid === track.cid
  return current.bvid === track.bvid
}

function playAll() {
  if (playlist.value?.tracks.length) player.playList(playlist.value.tracks, 0)
}

function saveMeta() {
  if (!playlist.value) return
  const name = draftName.value.trim()
  if (!name) {
    showMessage('名称不能为空', 'error')
    return
  }
  library.updatePlaylist(playlist.value.id, { name })
  showMessage('集合信息已保存')
}

async function addInputTracks() {
  const target = playlist.value
  const value = addInput.value.trim()
  if (!target || !value || addLoading.value) return
  addLoading.value = true
  showMessage('')
  try {
    const detail = await resolveTrackInput(value)
    const tracks = detail.pages.length > 1 ? detail.pages : [detail.pages[0] ?? detail.track]
    library.addTracksToPlaylist(target.id, tracks)
    addInput.value = ''
    showMessage(tracks.length > 1 ? `已添加 ${tracks.length} 个分 P` : '已添加 1 首')
  } catch (error) {
    showMessage(error instanceof Error ? error.message : '添加失败', 'error')
  } finally {
    addLoading.value = false
  }
}

function removeTrack(index: number) {
  const current = playlist.value
  if (!current) return
  const next = current.tracks.filter((_, i) => i !== index)
  library.replacePlaylistTracks(current.id, next)
}

function moveTrack(from: number, index: number) {
  const current = playlist.value
  if (!current || from === index) return
  const next = [...current.tracks]
  const [moved] = next.splice(from, 1)
  next.splice(index, 0, moved)
  library.replacePlaylistTracks(current.id, next)
}

function remove() {
  if (!playlist.value) return
  if (window.confirm(`确定删除歌单「${playlist.value.name}」？`)) {
    library.removePlaylist(playlist.value.id)
    router.push('/')
  }
}

function showMessage(value: string, kind: 'info' | 'error' = 'info') {
  message.value = value
  messageKind.value = kind
}
</script>

<style scoped>
.page {
  padding: 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.detail-top {
  display: flex;
  gap: 24px;
  align-items: flex-end;
}

.detail-cover {
  width: 180px;
  height: 180px;
  border-radius: var(--radius-small);
  overflow: hidden;
  background: var(--color-bg-hover);
  box-shadow: var(--shadow-popup);
  flex-shrink: 0;
}

.detail-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-tertiary);
}

.detail-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-kind {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.title-input,
.add-track-form input {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-content);
  color: var(--color-text-primary);
  outline: none;
  transition: border-color 160ms ease, background 160ms ease;
}

.title-input {
  width: min(560px, 100%);
  height: 42px;
  padding: 0 12px;
  font-size: 24px;
  font-weight: 700;
}

.title-input:focus,
.add-track-form input:focus {
  border-color: var(--color-primary);
  background: var(--color-bg-app);
}

.detail-meta {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.detail-actions,
.add-track-form {
  display: flex;
  align-items: center;
  gap: 10px;
}

.detail-actions {
  margin-top: 4px;
  flex-wrap: wrap;
}

.add-track-form {
  min-height: 44px;
  padding: 6px 6px 6px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-app);
}

.add-track-form input {
  flex: 1;
  min-width: 0;
  height: 34px;
  padding: 0 10px;
}

.primary-btn,
.ghost-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 36px;
  padding: 0 14px;
  border-radius: var(--radius-small);
  font-size: 13px;
  cursor: pointer;
  transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
}

.primary-btn {
  border: none;
  background: var(--color-primary);
  color: #fff;
}

.primary-btn:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.primary-btn:disabled,
.ghost-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ghost-btn {
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-text-primary);
}

.ghost-btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.ghost-btn.danger:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.page-message {
  margin-top: -8px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.page-message.error {
  color: var(--color-primary);
}

.result-list {
  display: flex;
  flex-direction: column;
}

.draggable-row {
  position: relative;
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  align-items: center;
  border-radius: var(--radius-small);
}

.draggable-row.dragging {
  opacity: 0.55;
}

.draggable-row.drop-target {
  background: var(--color-primary-soft);
}

.drag-handle {
  display: grid;
  place-items: center;
  width: 26px;
  height: 64px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: grab;
  user-select: none;
  touch-action: none;
}

.drag-handle:active {
  cursor: grabbing;
}

.empty-link {
  color: var(--color-primary);
  text-decoration: none;
  font-size: 14px;
}

@media (max-width: 720px) {
  .page {
    padding: 18px 16px;
  }

  .detail-top {
    align-items: flex-start;
    gap: 14px;
  }

  .detail-cover {
    width: 92px;
    height: 92px;
  }

  .title-input {
    height: 36px;
    font-size: 18px;
  }
}
</style>
