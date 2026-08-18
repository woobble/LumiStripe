/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

declare global {
  interface Window {
    __lumistripeUpdatePwa?: () => Promise<void>
  }
}

export {}
