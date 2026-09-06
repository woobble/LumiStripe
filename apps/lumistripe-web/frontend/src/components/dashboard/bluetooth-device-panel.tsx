import { BluetoothIcon, CircleAlertIcon, RefreshCwIcon, SaveIcon, SearchIcon, Trash2Icon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import type { BluetoothAction } from "@/hooks/use-bluetooth"
import { dashboardApi, type BluetoothOperation, type BluetoothStatusResponse } from "@/lib/api"

interface BluetoothDevicePanelProps {
  status: BluetoothStatusResponse | undefined
  alias: string
  busy: boolean
  onAliasChange: (alias: string) => void
  onAliasSave: () => void
  onAction: (action: BluetoothAction, operation: () => Promise<BluetoothStatusResponse>, message?: string) => void
  onRefresh: () => void
}

function supports(status: BluetoothStatusResponse | undefined, operation: BluetoothOperation) {
  // An older backend response has no capability block. Keep those clients
  // usable while the server rolls forward to the capability-aware contract.
  return status?.capabilities?.operations.length
    ? status.capabilities.operations.includes(operation)
    : true
}

function canConnect(status: BluetoothStatusResponse | undefined, role: "input" | "output") {
  if (!supports(status, "connect")) return false
  if (!status) return true
  const maximum = role === "input" ? status.capabilities.max_inputs : status.capabilities.max_outputs
  const connected = role === "input" ? status.connected_inputs.length : status.connected_outputs.length
  return connected < maximum
}

function bluetoothLabel(status: BluetoothStatusResponse | undefined) {
  if (!status?.available) return "Unavailable"
  if (status.operation_state === "failed") return "Action failed"
  if (status.operation?.startsWith("power:")) return status.operation.endsWith(":on") ? "Enabling…" : "Disabling…"
  if (status.operation === "renaming") return "Renaming…"
  if (!status.powered) return "Off"
  if (status.operation?.startsWith("pairing:")) return "Pairing…"
  if (status.operation?.startsWith("connecting:")) return "Connecting…"
  if (status.operation === "scanning" || status.scanning) return "Scanning…"
  if (status.streaming) return "Input streaming"
  if (status.connected_inputs.length || status.connected_outputs.length) return "Connected"
  return "Ready to pair"
}

export function BluetoothDevicePanel({
  status,
  alias,
  busy,
  onAliasChange,
  onAliasSave,
  onAction,
  onRefresh,
}: BluetoothDevicePanelProps) {
  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><BluetoothIcon className="size-4 text-violet-300" />Bluetooth devices</CardTitle>
            <CardDescription>Pair phones, speakers, and other Bluetooth audio devices. Role badges describe the direction relative to the Pi.</CardDescription>
          </div>
          <Badge variant={status?.streaming ? "default" : "outline"}>{bluetoothLabel(status)}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between gap-3 rounded-xl border border-white/5 bg-white/[0.03] p-3">
          <div className="min-w-0">
            <div className="text-sm font-medium">Bluetooth radio</div>
            <p className="text-xs text-muted-foreground">Turn Bluetooth off to disconnect devices and stop new connections.</p>
          </div>
          <Switch
            checked={Boolean(status?.powered)}
            disabled={!status?.available || busy || !supports(status, "power")}
            onCheckedChange={(checked) => onAction(`power:${checked ? "on" : "off"}`, () => dashboardApi.setBluetoothPower(checked), checked ? "Bluetooth enabled." : "Bluetooth disabled.")}
            aria-label="Enable Bluetooth"
          />
        </div>
        <div className="space-y-2">
          <label htmlFor="bluetooth-alias" className="text-sm font-medium">Visible Bluetooth name</label>
          <div className="flex gap-2">
            <Input id="bluetooth-alias" value={alias} maxLength={64} disabled={!status?.available || busy || !supports(status, "rename")} onChange={(event) => onAliasChange(event.target.value)} placeholder="LumiStripe" className="h-11 min-w-0 rounded-xl" />
            <Button variant="outline" className="h-11 shrink-0 rounded-xl" disabled={!status?.available || busy || !alias.trim() || alias.trim() === status?.adapter_alias || !supports(status, "rename")} onClick={onAliasSave}><SaveIcon />Save name</Button>
          </div>
          <p className="text-xs text-muted-foreground">This is the name phones, speakers, and other Bluetooth devices see when connecting to the Pi.</p>
        </div>
        <div className="flex gap-2">
          <Button className="h-11 flex-1 rounded-xl" disabled={busy || !status?.available || !status?.powered || !supports(status, "scan")} onClick={() => onAction("scan", dashboardApi.scanBluetooth, "Bluetooth scan started.")}><SearchIcon />{status?.scanning ? "Scanning…" : "Scan for devices"}</Button>
          <Button variant="outline" size="icon" className="size-11 rounded-xl" disabled={busy} onClick={onRefresh} aria-label="Refresh Bluetooth status"><RefreshCwIcon /></Button>
        </div>
        {(status?.connected_inputs.length || status?.connected_outputs.length) ? <div className="grid gap-2 sm:grid-cols-2">
          {status.connected_inputs.map((device) => <div key={`input-${device.address}`} className="rounded-xl border border-cyan-300/15 bg-cyan-300/5 p-3"><div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-medium">{device.name}</span><Badge variant="outline">Input</Badge></div><p className="mt-1 text-xs text-muted-foreground">{status.streaming ? "Music stream detected" : "Connected; waiting for audio"}</p></div>)}
          {status.connected_outputs.map((device) => <div key={`output-${device.address}`} className="rounded-xl border border-emerald-300/15 bg-emerald-300/5 p-3"><div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-medium">{device.name}</span><Badge variant="outline">Output</Badge></div><p className="mt-1 text-xs text-muted-foreground">Connected speaker output</p></div>)}
        </div> : null}
        <div className="space-y-2">
          {status?.devices.map((device) => (
            <div key={device.address} className="flex items-start gap-3 rounded-xl border border-white/5 bg-white/[0.03] p-3">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{device.name}</div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  {device.roles.map((role) => <Badge key={role} variant="outline">{role === "input" ? "Input" : "Output"}</Badge>)}
                  <span className="text-xs text-muted-foreground">{device.address}{device.connected ? " · Connected" : device.paired ? " · Paired" : " · New"}</span>
                </div>
              </div>
              <div className="flex max-w-[62%] shrink-0 flex-wrap justify-end gap-2">
                {device.connected ? (
                  <>
                    <Badge>Connected</Badge>
                    {supports(status, "disconnect") && <Button variant="outline" className="h-9 rounded-lg px-3 text-xs" disabled={busy} onClick={() => onAction(`disconnect:${device.address}`, () => dashboardApi.disconnectBluetooth(device.address), "Bluetooth device disconnected.")}>Disconnect</Button>}
                  </>
                ) : !device.paired ? (
                  supports(status, "pair") && <Button variant="outline" className="h-9 rounded-lg px-3 text-xs" disabled={busy || !status?.powered} onClick={() => onAction(`pair:${device.address}`, () => dashboardApi.pairBluetooth(device.address), "Pairing started. Keep the device's Bluetooth settings open.")}>Pair</Button>
                ) : device.roles.length > 0 ? (
                  device.roles.map((role) => <Button key={role} variant="outline" className="h-9 rounded-lg px-3 text-xs" disabled={busy || !status?.powered || !canConnect(status, role)} onClick={() => onAction(`connect:${role}:${device.address}`, () => dashboardApi.connectBluetooth(device.address, role), `Connecting as ${role}…`)}>Connect as {role}</Button>)
                ) : (
                  <Button variant="outline" className="h-9 rounded-lg px-3 text-xs" disabled={busy || !status?.powered || !canConnect(status, "input")} onClick={() => onAction(`connect:default:${device.address}`, () => dashboardApi.connectBluetooth(device.address), "Connecting to Bluetooth device…")}>Connect</Button>
                )}
                {device.paired && supports(status, "forget") && <Button variant="ghost" size="icon" className="size-9 shrink-0 rounded-lg" disabled={busy} onClick={() => onAction(`forget:${device.address}`, () => dashboardApi.forgetBluetooth(device.address), "Bluetooth device removed.")} aria-label={`Forget ${device.name}`}><Trash2Icon /></Button>}
              </div>
            </div>
          ))}
        </div>
        {!status?.available && <div className="flex gap-2 rounded-xl bg-amber-500/10 p-3 text-sm text-amber-100"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>Bluetooth tools are unavailable. Install the Pi Bluetooth/PipeWire setup from the deployment guide.</span></div>}
        {status?.error && <div className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>{status.error}</span></div>}
        {status?.available && !status.powered && !status.operation && <p className="text-sm text-muted-foreground">Bluetooth is off. Turn it on to scan, pair, or connect a device.</p>}
        {status?.available && status.powered && !status.devices.length && !status.operation && <p className="text-sm text-muted-foreground">No devices found yet. Put a phone, speaker, or other Bluetooth audio device in pairing mode, then scan.</p>}
        <p className="text-xs text-muted-foreground">Input devices send music to the Pi for animation analysis. Output devices receive the Pi’s audio stream.</p>
      </CardContent>
    </Card>
  )
}
