import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from 'next-themes'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { registerSW } from 'virtual:pwa-register'
import './index.css'
import App from './App.tsx'
import { reloadLatestFrontend } from './pwa-update'

const router = createBrowserRouter([{ path: '*', element: <App /> }])

const updateServiceWorker = registerSW({
  onNeedRefresh() {
    window.dispatchEvent(new Event('lumistripe:pwa-update'))
  },
})

window.__lumistripeUpdatePwa = () => updateServiceWorker(true)
window.__lumistripeReloadLatestPwa = () => reloadLatestFrontend(updateServiceWorker)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider attribute="class" forcedTheme="dark" disableTransitionOnChange>
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
)
