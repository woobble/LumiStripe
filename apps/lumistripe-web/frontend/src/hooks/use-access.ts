import { useCallback, useEffect, useState } from "react"

import { ACCESS_REVOKED_EVENT, accessApi, type AccessStatus } from "@/lib/api"

const unknownAccess: AccessStatus = { required: true, authenticated: false }

export function useAccess() {
  const [status, setStatus] = useState<AccessStatus>(unknownAccess)
  const [loading, setLoading] = useState(true)
  const [pairing, setPairing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setStatus(await accessApi.status())
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not check dashboard access.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const revoke = () => setStatus({ required: true, authenticated: false })
    window.addEventListener(ACCESS_REVOKED_EVENT, revoke)
    return () => window.removeEventListener(ACCESS_REVOKED_EVENT, revoke)
  }, [])

  const pair = async (code: string) => {
    setPairing(true)
    setError(null)
    try {
      const nextStatus = await accessApi.pair(code)
      setStatus(nextStatus)
      return true
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Pairing failed.")
      return false
    } finally {
      setPairing(false)
    }
  }

  const logout = async () => {
    try {
      setStatus(await accessApi.logout())
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not log out.")
    }
  }

  return { ...status, loading, pairing, error, refresh, pair, logout }
}

export type AccessController = ReturnType<typeof useAccess>
