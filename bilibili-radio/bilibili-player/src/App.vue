<template>
  <RouterView v-if="isAuthLayout || isOverlayLayout" />
  <AppShell v-else />
  <DesktopLyricsBridge v-if="!isAuthLayout && !isOverlayLayout" />
  <DesktopControlBridge v-if="!isAuthLayout && !isOverlayLayout" />
  <Transition name="nowplaying">
    <NowPlayingView v-if="!isAuthLayout && !isOverlayLayout && ui.nowPlayingOpen" />
  </Transition>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { usePlayerStore } from '@/stores/playerStore'
import { useLibraryStore } from '@/stores/libraryStore'
import { useUiStore } from '@/stores/uiStore'
import { useAuthStore } from '@/stores/authStore'
import AppShell from '@/components/layout/AppShell.vue'
import DesktopLyricsBridge from '@/components/DesktopLyricsBridge.vue'
import DesktopControlBridge from '@/components/DesktopControlBridge.vue'
import NowPlayingView from '@/views/NowPlayingView.vue'

const route = useRoute()
const player = usePlayerStore()
const library = useLibraryStore()
const ui = useUiStore()
const auth = useAuthStore()

const isAuthLayout = computed(() => route.meta.layout === 'auth')
const isOverlayLayout = computed(() => route.meta.layout === 'overlay')

watch(
  [isAuthLayout, isOverlayLayout, () => auth.appAuthenticated],
  () => initializeAppServices(),
  { immediate: true }
)

function initializeAppServices() {
  if (isAuthLayout.value || isOverlayLayout.value || !auth.appAuthenticated) return
  void auth.initializeBili()
  void library.initialize()
  void player.initialize()
}
</script>

<style>
.nowplaying-enter-active,
.nowplaying-leave-active {
  transition: opacity 260ms ease, transform 260ms ease;
}
.nowplaying-enter-from,
.nowplaying-leave-to {
  opacity: 0;
  transform: translateY(24px);
}

@media (prefers-reduced-motion: reduce) {
  .nowplaying-enter-active,
  .nowplaying-leave-active {
    transition: opacity 160ms ease;
  }
  .nowplaying-enter-from,
  .nowplaying-leave-to {
    transform: none;
  }
}
</style>
