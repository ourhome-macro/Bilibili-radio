/** Stable identity lookup keeps locating correct after filtering or reordering. */
export function locateTrack(container: HTMLElement | null, trackId: string | null): boolean {
  if (!container || !trackId) return false
  const row = Array.from(container.querySelectorAll<HTMLElement>('[data-track-id]'))
    .find(element => element.dataset.trackId === trackId)
  if (!row) return false
  row.scrollIntoView({ block: 'center', behavior: 'auto' })
  return true
}
