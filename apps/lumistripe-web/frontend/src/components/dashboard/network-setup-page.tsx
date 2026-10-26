import { useEffect, useMemo, useState } from "react"
import { RefreshCwIcon, WifiIcon, WifiOffIcon } from "lucide-react"

import { SetupPage } from "@/components/dashboard/setup-nav"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { dashboardApi, type NetworkStatusResponse, type WifiNetworkInfo } from "@/lib/api"

function signalLabel(signal: number) {
  if (signal >= 75) return "Strong signal"
  if (signal >= 45) return "Good signal"
  return "Weak signal"
}

export function NetworkSetupPage() {
  const [status, setStatus] = useState<NetworkStatusResponse | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [interfaceName, setInterfaceName] = useState("")
  const [networks, setNetworks] = useState<WifiNetworkInfo[]>([])
  const [ssid, setSsid] = useState("")
  const [password, setPassword] = useState("")
  const [operationError, setOperationError] = useState<string | null>(null)
  const [connectedMessage, setConnectedMessage] = useState<string | null>(null)

  const selectedAdapter = useMemo(
    () => status?.adapters.find((adapter) => adapter.interface === interfaceName),
    [interfaceName, status],
  )
  const selectedNetwork = networks.find((network) => network.ssid === ssid)

  useEffect(() => {
    const controller = new AbortController()
    void dashboardApi.getNetworkStatus(controller.signal).then((next) => {
      setStatus(next)
      const initialAdapter = next.adapters.find((adapter) => adapter.connectable)
      setInterfaceName((current) => current || initialAdapter?.interface || "")
      setStatusError(null)
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setStatusError(error instanceof Error ? error.message : "Could not load Wi-Fi adapters.")
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [])

  const refreshStatus = async () => {
    setLoading(true)
    setStatusError(null)
    try {
      const next = await dashboardApi.getNetworkStatus()
      setStatus(next)
      if (!next.adapters.some((adapter) => adapter.interface === interfaceName && adapter.connectable)) {
        setInterfaceName(next.adapters.find((adapter) => adapter.connectable)?.interface ?? "")
      }
    } catch (error) {
      setStatusError(error instanceof Error ? error.message : "Could not load Wi-Fi adapters.")
    } finally {
      setLoading(false)
    }
  }

  const scanNetworks = async () => {
    if (!selectedAdapter?.connectable || busy) return
    setBusy(true)
    setOperationError(null)
    setConnectedMessage(null)
    try {
      const result = await dashboardApi.scanWifi(interfaceName)
      setNetworks(result.networks)
      setSsid((current) => result.networks.some((network) => network.ssid === current)
        ? current
        : result.networks[0]?.ssid ?? "")
      setPassword("")
      if (result.networks.length === 0) {
        setOperationError("No nearby networks were found. Move the Pi closer to your router and scan again.")
      }
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Wi-Fi scan failed.")
    } finally {
      setBusy(false)
    }
  }

  const connect = async () => {
    if (!selectedNetwork || !selectedAdapter?.connectable || busy) return
    setBusy(true)
    setOperationError(null)
    setConnectedMessage(null)
    try {
      await dashboardApi.connectWifi(interfaceName, selectedNetwork.ssid, password)
      setConnectedMessage(`Connected to ${selectedNetwork.ssid} on ${interfaceName}.`)
      setPassword("")
      try {
        const next = await dashboardApi.getNetworkStatus()
        setStatus(next)
        setStatusError(null)
      } catch (error) {
        setStatusError(error instanceof Error ? error.message : "Could not refresh adapter status.")
      }
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Could not connect to this network.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <SetupPage>
      <Card className="border-white/5 bg-card/80">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><WifiIcon aria-hidden="true" className="size-4" /> Internet Wi-Fi</CardTitle>
          <CardDescription>Connect the internet adapter to Wi-Fi for updates. The hotspot used by dashboard devices stays on its own adapter.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading && !status ? <Skeleton className="h-24 rounded-xl" aria-label="Loading Wi-Fi adapters" /> : (
            <>
              {statusError && <p role="alert" className="rounded-lg bg-red-500/10 p-3 text-sm text-red-200">{statusError}</p>}
              {status && !status.manager_available && <p role="alert" className="rounded-lg bg-amber-500/10 p-3 text-sm text-amber-100">{status.error ?? "NetworkManager is unavailable."}</p>}
              {status && status.manager_available && status.adapters.length === 0 && <p className="rounded-lg bg-white/[0.04] p-3 text-sm text-muted-foreground">No Wi-Fi adapters were found.</p>}

              {status?.adapters.length ? (
                <div className="space-y-2">
                  <label htmlFor="wifi-adapter" className="text-sm font-medium">Internet adapter</label>
                  <Select value={interfaceName} onValueChange={(value) => {
                    setInterfaceName(value ?? "")
                    setNetworks([])
                    setSsid("")
                    setPassword("")
                    setOperationError(null)
                    setConnectedMessage(null)
                  }} disabled={busy || !status.manager_available}>
                    <SelectTrigger id="wifi-adapter" aria-label="Internet adapter" className="h-11 w-full rounded-xl">
                      <SelectValue>{selectedAdapter ? `${selectedAdapter.interface}${selectedAdapter.hotspot ? " · Hotspot (protected)" : selectedAdapter.current_ssid ? ` · Connected to ${selectedAdapter.current_ssid}` : " · Available"}` : "Select an adapter"}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {status.adapters.map((adapter) => (
                        <SelectItem key={adapter.interface} value={adapter.interface} disabled={!adapter.connectable}>
                          {adapter.interface}{adapter.hotspot ? " · Hotspot (protected)" : adapter.connectable ? adapter.current_ssid ? ` · ${adapter.current_ssid}` : " · Available" : " · Unavailable"}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selectedAdapter?.reason && <p className="text-xs text-amber-200">{selectedAdapter.reason}</p>}
                  {selectedAdapter?.current_ssid && <p className="flex items-center gap-2 text-sm text-emerald-200"><WifiIcon aria-hidden="true" className="size-4" /> Connected to {selectedAdapter.current_ssid}</p>}
                  {selectedAdapter && !selectedAdapter.current_ssid && selectedAdapter.state.toLowerCase().includes("unavailable") && <p className="flex items-center gap-2 text-sm text-muted-foreground"><WifiOffIcon aria-hidden="true" className="size-4" /> Adapter is currently unavailable.</p>}
                </div>
              ) : null}

              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" variant="outline" disabled={busy || !selectedAdapter?.connectable || !status?.manager_available} onClick={() => { void scanNetworks() }}>
                  <RefreshCwIcon aria-hidden="true" className={busy ? "animate-spin" : ""} />
                  {busy ? "Working…" : networks.length ? "Scan again" : "Scan for networks"}
                </Button>
                <Button type="button" variant="ghost" disabled={loading || busy} onClick={() => { void refreshStatus() }}>Refresh adapters</Button>
              </div>

              {networks.length > 0 && (
                <div className="space-y-3 border-t border-white/5 pt-4">
                  <div className="space-y-2">
                    <label htmlFor="wifi-network" className="text-sm font-medium">Wi-Fi network</label>
                    <Select value={ssid} onValueChange={(value) => {
                      setSsid(value ?? "")
                      setPassword("")
                      setOperationError(null)
                      setConnectedMessage(null)
                    }} disabled={busy}>
                      <SelectTrigger id="wifi-network" aria-label="Wi-Fi network" className="h-11 w-full rounded-xl">
                        <SelectValue>{selectedNetwork ? `${selectedNetwork.ssid} · ${selectedNetwork.security} · ${signalLabel(selectedNetwork.signal)}` : "Select a network"}</SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {networks.map((network) => <SelectItem key={network.ssid} value={network.ssid}>{network.ssid} · {network.security} · {signalLabel(network.signal)}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  {selectedNetwork?.secured && (
                    <div className="space-y-2">
                      <label htmlFor="wifi-password" className="text-sm font-medium">Wi-Fi password</label>
                      <Input id="wifi-password" type="password" autoComplete="new-password" maxLength={63} value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} className="h-11 rounded-xl" />
                      <p className="text-xs text-muted-foreground">The password is sent securely to NetworkManager and is not saved in LumiStripe.</p>
                    </div>
                  )}
                  <Button type="button" disabled={busy || !selectedNetwork || Boolean(selectedNetwork?.secured && password.length < 8)} onClick={() => { void connect() }}>
                    Connect
                  </Button>
                </div>
              )}

              {operationError && <p role="alert" className="rounded-lg bg-red-500/10 p-3 text-sm text-red-200">{operationError}</p>}
              {connectedMessage && <p role="status" className="rounded-lg bg-emerald-500/10 p-3 text-sm text-emerald-100">{connectedMessage}</p>}
              <p className="text-xs text-muted-foreground">Saved networks reconnect automatically. Hotspot adapters and adapters whose mode cannot be verified are blocked from connection changes.</p>
            </>
          )}
        </CardContent>
      </Card>
    </SetupPage>
  )
}
