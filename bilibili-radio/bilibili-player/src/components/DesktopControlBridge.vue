<template>
  <div v-if="notice" class="desktop-notice" role="status">
    {{ notice }}
    <button type="button" @click="notice = ''">知道了</button>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { usePlayerStore } from '@/stores/playerStore'
import { useUiStore } from '@/stores/uiStore'

const player = usePlayerStore()
const ui = useUiStore()
const notice = ref('')
let unlisten: (() => void) | undefined
let disposed = false

onMounted(async () => {
  if (window.location.hostname !== 'tauri.localhost' && window.location.protocol !== 'tauri:') return
  const { listen } = await import('@tauri-apps/api/event')
  const { invoke } = await import('@tauri-apps/api/core')
  const remove = await listen<string>('desktop:control', async ({ payload }) => {
    switch (payload) {
      case 'toggle-play': player.togglePlayPause(); break
      case 'prev': player.prev(); break
      case 'next': player.next(); break
      case 'locate': ui.locateCurrentInQueue(); break
      case 'lyrics-close': ui.setLyricsOverlayEnabled(false); break
      case 'checkpoint': void player.flushProgress(); break
      case 'quit':
        try { await player.prepareForExit() }
        finally { await invoke('finish_desktop_exit') }
        break
    }
  })
  if (disposed) remove()
  else unlisten = remove
  notice.value = (await invoke<string[]>('desktop_control_status')).join('；')
})

onBeforeUnmount(() => { disposed = true; unlisten?.() })
</script>

<style scoped>
.desktop-notice { position: fixed; bottom: 96px; right: 20px; z-index: 100; max-width: 460px; padding: 14px; background: var(--color-bg-content); color: var(--color-text-primary); border: 1px solid var(--color-primary); border-radius: 8px; box-shadow: var(--shadow-popup); }
.desktop-notice button { margin-left: 12px; cursor: pointer; }
</style>
