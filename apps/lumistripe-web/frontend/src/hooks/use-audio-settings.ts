import { useCallback, useEffect, useRef, useState } from "react"

import { dashboardApi, type AudioSettingsResponse } from "@/lib/api"

export function useAudioSettings() {
  const [settings, setSettings] = useState<AudioSettingsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const requestVersion = useRef(0)
  const mounted = useRef(false)

  const refresh = useCallback(async (showLoading = true) => {
    const version = ++requestVersion.current
    if (mounted.current && showLoading) {
      setLoading(true)
      setLoadError(null)
    }
    try {
      const next = await dashboardApi.getAudioSettings()
      if (mounted.current && version === requestVersion.current) {
        setSettings(next)
        setLoadError(null)
      }
      return next
    } catch (error) {
      if (mounted.current && version === requestVersion.current) {
        setLoadError(error instanceof Error ? error.message : "Could not load audio settings.")
      }
      throw error
    } finally {
      if (mounted.current && version === requestVersion.current) setLoading(false)
    }
  }, [])

  const applySettings = useCallback((next: AudioSettingsResponse) => {
    requestVersion.current += 1
    if (!mounted.current) return
    setSettings(next)
    setLoadError(null)
  }, [])

  useEffect(() => {
    mounted.current = true
    void refresh(false).catch(() => undefined)
    return () => {
      mounted.current = false
      requestVersion.current += 1
    }
  }, [refresh])

  return { settings, loading, loadError, refresh, applySettings }
}
