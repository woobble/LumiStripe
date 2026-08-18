import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from 'next-themes'
import { BrowserRouter } from 'react-router'
import { registerSW } from 'virtual:pwa-register'
import './index.css'
import App from './App.tsx'

const updateServiceWorker = registerSW({
  onNeedRefresh() {
    window.dispatchEvent(new Event('lumistripe:pwa-update'))
  },
})

window.__lumistripeUpdatePwa = () => updateServiceWorker(true)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider attribute="class" forcedTheme="dark" disableTransitionOnChange>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ThemeProvider>
  </StrictMode>,
)
