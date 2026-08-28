import { configureApiBaseUrl } from '@/api/client'

const DEFAULT_DESKTOP_API_BASE_URL = 'http://127.0.0.1:41517'
let desktopRuntime = false

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown
  }
}

export async function initializeDesktopRuntime(): Promise<void> {
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    const endpoint = await invoke<string>('desktop_backend_endpoint')
    configureApiBaseUrl(endpoint)
    desktopRuntime = true
  } catch {
    const fallbackEndpoint = resolveDesktopFallbackEndpoint()
    configureApiBaseUrl(fallbackEndpoint)
    desktopRuntime = Boolean(fallbackEndpoint)
  }
}

export function isDesktopRuntime(): boolean {
  return desktopRuntime || hasTauriLocation() || Boolean(window.__TAURI_INTERNALS__)
}

function resolveDesktopFallbackEndpoint(): string | null {
  return hasTauriLocation() ? DEFAULT_DESKTOP_API_BASE_URL : null
}

function hasTauriLocation(): boolean {
  return window.location.protocol === 'tauri:' || window.location.hostname === 'tauri.localhost'
}
