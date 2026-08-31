import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'node:path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      // Keep the current dashboard session alive until the user explicitly
      // accepts an update. This prevents a service-worker update from
      // unexpectedly reloading every mounted component.
      registerType: 'prompt',
      includeAssets: ['favicon.png', 'apple-touch-icon-v3.png', 'icons/lumistripe-192.png', 'icons/lumistripe-512.png'],
      manifest: {
        name: 'LumiStripe Dashboard',
        short_name: 'LumiStripe',
        description: 'Control LumiStripe from your phone.',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        orientation: 'portrait-primary',
        theme_color: '#13101d',
        background_color: '#13101d',
        icons: [
          { src: '/icons/lumistripe-192.png', sizes: '192x192', type: 'image/png', purpose: 'any maskable' },
          { src: '/icons/lumistripe-512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
        ],
      },
      workbox: {
        navigateFallback: '/index.html',
        globPatterns: ['**/*.{js,css,html,svg,woff2,png}'],
        navigateFallbackDenylist: [/^\/api\//, /^\/ws\//],
      },
    }),
  ],
  build: {
    outDir: '../src/lumistripe_web/static',
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    host: '0.0.0.0',
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true
      }
    }
  }
})
