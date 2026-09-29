import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick, reactive } from 'vue'
import QueueDrawer from './QueueDrawer.vue'
import { useUiStore } from '@/stores/uiStore'

const mocked = vi.hoisted(() => ({ player: null as any }))
vi.mock('@/stores/playerStore', () => ({ usePlayerStore: () => mocked.player }))
vi.mock('@/stores/libraryStore', () => ({ useLibraryStore: () => ({ isTrackLiked: () => false, toggleLike: vi.fn() }) }))
const scrolled: string[] = []
let wrapper: ReturnType<typeof mount>
beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  const queue = Array.from({ length: 100 }, (_, i) => ({ bvid: 'BV1Q541167Qg', cid: i, title: `Episode ${i}`, owner: '', cover: '', duration: 100 }))
  mocked.player = reactive({ queue, currentIndex: 79, get currentTrack() { return this.queue[this.currentIndex] }, isPlaying: false, moveQueueItem: vi.fn(), clearQueue: vi.fn() })
  scrolled.length = 0
  HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this.dataset.trackId ?? '') }
  wrapper = mount(QueueDrawer, { attachTo: document.body })
})
afterEach(() => { wrapper.unmount(); document.body.innerHTML = '' })
async function render() { await nextTick(); await nextTick(); await nextTick() }

describe('locate current queue track', () => {
  it('locates a late current row when opening a long queue, including while paused', async () => {
    useUiStore().toggleQueue(); await render()
    expect(scrolled.at(-1)).toBe('bili:BV1Q541167Qg:cid:79')
  })
  it('explicit locate clears a filter hiding the current track', async () => {
    useUiStore().toggleQueue(); await render()
    const input = document.querySelector('input')!
    input.value = 'Episode 1'; input.dispatchEvent(new Event('input', { bubbles: true })); await render()
    expect(document.querySelector('.queue-row.current')).toBeNull()
    ;(document.querySelector('[title="定位当前播放"]') as HTMLButtonElement).click(); await render()
    expect(input.value).toBe('')
    expect(scrolled.at(-1)).toBe('bili:BV1Q541167Qg:cid:79')
  })
  it('does not pull back a user browsing the list until explicitly locating', async () => {
    useUiStore().toggleQueue(); await render()
    document.querySelector('.drawer-body')!.dispatchEvent(new Event('wheel', { bubbles: true }))
    scrolled.length = 0
    mocked.player.currentIndex = 80; await render()
    expect(scrolled).toEqual([])
    useUiStore().locateCurrentInQueue(); await render()
    expect(scrolled.at(-1)).toBe('bili:BV1Q541167Qg:cid:80')
  })
})
