import { useEffect, useRef, useState, type FocusEvent, type ReactNode } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { ArrowDownIcon, ArrowUpIcon, CableIcon, ChevronDownIcon, PlusIcon, SaveIcon, Trash2Icon, ZapIcon } from "lucide-react"
import { toast } from "sonner"
import { z } from "zod"

import { SetupPage } from "@/components/dashboard/setup-nav"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Toggle } from "@/components/ui/toggle"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import type { DashboardController } from "@/hooks/use-dashboard"
import type { StripeBackend, StripeLayout, StripeOutputConfig, StripeTopology } from "@/lib/api"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"

const layouts: Array<{ value: StripeLayout; label: string; detail: string }> = [
  { value: "mirrored", label: "Mirror", detail: "Same scene, scaled to each length" },
  { value: "continuous", label: "Continuous", detail: "One scene across both strips" },
  { value: "independent", label: "Independent", detail: "Separate controls for every strip" },
]

const stripeTopologySchema = z.object({
  layout: z.enum(["mirrored", "continuous", "independent"]),
  power_budget_enabled: z.boolean(),
  power_budget_watts: z.number().positive("Set a positive wagon power limit.").nullable(),
  shared_supply_enabled: z.boolean(),
  supply_capacity_watts: z.number().positive("Supply capacity must be greater than zero."),
  pi_reserve_watts: z.number().nonnegative("Pi reserve must not be negative."),
  outputs: z.array(z.object({
    id: z.string().min(1),
    name: z.string().trim().min(1, "Every stripe needs a name.").max(40),
    pixels: z.number().int().min(1).max(4096),
    zone_count: z.number().int().min(1).max(4096),
    zone_pattern: z.enum(["repeat", "alternate"]),
    voltage_v: z.number().positive("Voltage must be greater than zero."),
    full_white_current_a: z.number().positive("Current must be greater than zero."),
    power_limit_watts: z.number().positive("Limit must be greater than zero.").nullable(),
  }).passthrough().refine((output) => output.zone_count <= output.pixels, {
    path: ["zone_count"],
    message: "Zones cannot exceed the stripe pixel count.",
  })).min(1).max(2),
}).refine((value) => !value.power_budget_enabled || value.power_budget_watts !== null, {
  path: ["power_budget_watts"],
  message: "Set a positive wagon power limit when the budget is enabled.",
})

function copyTopology(topology: StripeTopology): StripeTopology {
  return {
    layout: topology.layout,
    power_budget_enabled: topology.power_budget_enabled,
    power_budget_watts: topology.power_budget_watts,
    shared_supply_enabled: topology.shared_supply_enabled,
    supply_capacity_watts: topology.supply_capacity_watts,
    pi_reserve_watts: topology.pi_reserve_watts,
    outputs: topology.outputs.map((output) => ({
      ...output,
      zone_count: output.zone_count ?? 1,
      zone_pattern: output.zone_pattern ?? "repeat",
    })),
  }
}

function newStripeId() {
  const randomUUID = globalThis.crypto?.randomUUID
  if (typeof randomUUID === "function") return randomUUID.call(globalThis.crypto)
  return `stripe-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function newStripe(existing: StripeOutputConfig[]): StripeOutputConfig {
  return {
    id: newStripeId(),
    name: `Stripe ${existing.length + 1}`,
    pixels: existing[0]?.pixels ?? 41,
    backend: "spi",
    reversed: false,
    zone_count: 1,
    zone_pattern: "repeat",
    spi_device: existing.length === 0 ? "/dev/spidev0.0" : "/dev/spidev1.0",
    spi_speed_hz: 1_000_000,
    chip: "/dev/gpiochip0",
    data_pin: existing.length === 0 ? 10 : 20,
    clock_pin: existing.length === 0 ? 11 : 21,
    voltage_v: existing[0]?.voltage_v ?? 5,
    full_white_current_a: existing[0]?.full_white_current_a ?? 0.06,
    power_limit_watts: null,
  }
}

function zoneRanges(pixels: number, requestedCount: number) {
  const count = Math.max(1, Math.min(Math.trunc(requestedCount) || 1, pixels))
  const baseLength = Math.floor(pixels / count)
  const remainder = pixels % count
  let firstPixel = 1
  return Array.from({ length: count }, (_, index) => {
    const length = baseLength + (index < remainder ? 1 : 0)
    const range = { index: index + 1, firstPixel, lastPixel: firstPixel + length - 1 }
    firstPixel += length
    return range
  })
}

export function StripeManagementPanel({ controller }: { controller: DashboardController }) {
  const { state, pendingCommand } = controller
  const form = useForm<StripeTopology>({ resolver: zodResolver(stripeTopologySchema) as never, defaultValues: { layout: "mirrored", power_budget_enabled: false, power_budget_watts: null, shared_supply_enabled: false, supply_capacity_watts: 200, pi_reserve_watts: 15, outputs: [] } })
  const { control, watch, setValue, reset, handleSubmit, formState: { isDirty, errors } } = form
  const { fields, append, remove: removeField, move: moveField } = useFieldArray({ control, name: "outputs" })
  const draft = watch()
  useUnsavedChangesGuard(isDirty)
  const [removeIndex, setRemoveIndex] = useState<number | null>(null)
  const [formFocused, setFormFocused] = useState(false)
  const syncedTopologyRef = useRef<string | null>(null)
  const topology = state?.stripe_topology

  useEffect(() => {
    // reset() regenerates field-array keys, so wait for focus to leave before replacing controls.
    if (!topology || isDirty || formFocused) return
    const topologyKey = JSON.stringify(topology)
    if (topologyKey === syncedTopologyRef.current) return
    reset(copyTopology(topology))
    syncedTopologyRef.current = topologyKey
  }, [formFocused, isDirty, reset, topology])

  if (!state || !draft) return null
  const busy = pendingCommand !== null

  const updateOutput = (index: number, patch: Partial<StripeOutputConfig>) => {
    Object.entries(patch).forEach(([key, value]) => setValue(`outputs.${index}.${key}` as `outputs.${number}.${keyof StripeOutputConfig}`, value as never, { shouldDirty: true, shouldValidate: true }))
  }

  const apply = async (values: StripeTopology) => {
    const ok = await controller.updateStripes(values)
    if (ok) {
      reset(values)
      toast.success("Stripe configuration applied.")
    }
  }

  const remove = (index: number) => {
    removeField(index)
    setRemoveIndex(null)
  }

  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction
    if (target < 0 || target >= fields.length) return
    moveField(index, target)
  }

  const handleFormBlur = (event: FocusEvent<HTMLDivElement>) => {
    const container = event.currentTarget
    queueMicrotask(() => {
      setFormFocused(container.contains(document.activeElement))
    })
  }

  return (
    <SetupPage onFocusCapture={() => setFormFocused(true)} onBlurCapture={handleFormBlur}>
      <div className="grid gap-4 xl:grid-cols-2">
      <Card className="border-border/70 bg-card shadow-none">
        <CardHeader>
          <CardTitle>Layout</CardTitle>
          <CardDescription>Choose how animations use two outputs.</CardDescription>
        </CardHeader>
        <CardContent>
          <ToggleGroup aria-label="Stripe layout" value={[draft.layout]} onValueChange={(values) => { const value = values[0] as StripeLayout | undefined; if (value) setValue("layout", value, { shouldDirty: true, shouldValidate: true }) }} className="grid w-full grid-cols-3 gap-1 rounded-xl bg-black/20 p-1" variant="outline">
            {layouts.map((layout) => <ToggleGroupItem key={layout.value} value={layout.value} className="h-14 min-w-0 flex-col gap-0 rounded-lg px-1 text-xs"><span>{layout.label}</span></ToggleGroupItem>)}
          </ToggleGroup>
          <p className="mt-2 text-xs text-muted-foreground">{layouts.find((item) => item.value === draft.layout)?.detail}</p>
        </CardContent>
      </Card>

      <Card className="border-border/70 bg-card shadow-none">
        <CardHeader>
          <CardTitle>Power budget</CardTitle>
          <CardDescription>Estimate LED load and cap all outputs together to protect the wagon supply.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Toggle
            pressed={draft.power_budget_enabled}
            onPressedChange={(pressed) => setValue("power_budget_enabled", pressed, { shouldDirty: true, shouldValidate: true })}
            variant="outline"
            aria-label={`Power budget ${draft.power_budget_enabled ? "on" : "off"}`}
            className="h-11 w-full justify-between px-3 data-pressed:border-violet-300/30 data-pressed:bg-violet-400/15 data-pressed:text-violet-100"
          >
            <span>Limit total LED power</span>
            <span className="text-xs text-muted-foreground">{draft.power_budget_enabled ? "On" : "Off"}</span>
          </Toggle>
          {draft.power_budget_enabled && (
            <Field label="Wagon limit (watts)">
              <Input
                type="number"
                min={0.1}
                step={0.1}
                inputMode="decimal"
                value={draft.power_budget_watts ?? ""}
                aria-invalid={errors.power_budget_watts ? true : undefined}
                onChange={(event) => setValue("power_budget_watts", event.target.value === "" ? null : Number(event.target.value), { shouldDirty: true, shouldValidate: true })}
              />
              <InlineError message={errors.power_budget_watts?.message} />
            </Field>
          )}
          <Toggle
            pressed={draft.shared_supply_enabled}
            onPressedChange={(pressed) => setValue("shared_supply_enabled", pressed, { shouldDirty: true, shouldValidate: true })}
            variant="outline"
            aria-label={`Shared supply limit ${draft.shared_supply_enabled ? "on" : "off"}`}
            className="h-11 w-full justify-between px-3 data-pressed:border-violet-300/30 data-pressed:bg-violet-400/15 data-pressed:text-violet-100"
          >
            <span>Limit shared PSU power</span>
            <span className="text-xs text-muted-foreground">{draft.shared_supply_enabled ? "On" : "Off"}</span>
          </Toggle>
          {draft.shared_supply_enabled && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="PSU capacity (W)">
                <Input
                  type="number"
                  min={0.1}
                  step={1}
                  inputMode="decimal"
                  value={draft.supply_capacity_watts}
                  aria-invalid={errors.supply_capacity_watts ? true : undefined}
                  onChange={(event) => setValue("supply_capacity_watts", Number(event.target.value), { shouldDirty: true, shouldValidate: true })}
                />
                <InlineError message={errors.supply_capacity_watts?.message} />
              </Field>
              <Field label="Pi + buck input at PSU (W)">
                <Input
                  type="number"
                  min={0}
                  step={1}
                  inputMode="decimal"
                  value={draft.pi_reserve_watts}
                  aria-invalid={errors.pi_reserve_watts ? true : undefined}
                  onChange={(event) => setValue("pi_reserve_watts", Number(event.target.value), { shouldDirty: true, shouldValidate: true })}
                />
                <InlineError message={errors.pi_reserve_watts?.message} />
              </Field>
            </div>
          )}
          <p className="text-xs text-muted-foreground">The estimate uses each strip’s voltage and full-white current calibration. The cap changes output brightness without changing animation settings.</p>
          {draft.shared_supply_enabled && <p className="text-xs text-muted-foreground">LED allowance: {Math.max(0, draft.supply_capacity_watts - draft.pi_reserve_watts).toFixed(1)} W. Enter the Pi and converter draw at the PSU, including conversion losses. LumiStripe does not measure their power draw.</p>}
        </CardContent>
      </Card>
      </div>

      <div className="grid gap-4 2xl:grid-cols-2">
      {fields.map((field, index) => {
        const output = draft.outputs[index]
        if (!output) return null
        return <Card key={field.id} className="border-border/70 bg-card shadow-none">
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div><CardTitle>{output.name || `Stripe ${index + 1}`}</CardTitle><CardDescription>{output.backend === "spi" ? output.spi_device : `${output.chip} · GPIO ${output.data_pin}/${output.clock_pin}`}</CardDescription></div>
              <div className="flex gap-1">
                <Button variant="ghost" size="icon" disabled={index === 0 || busy} onClick={() => move(index, -1)} aria-label="Move stripe up"><ArrowUpIcon /></Button>
                <Button variant="ghost" size="icon" disabled={index === draft.outputs.length - 1 || busy} onClick={() => move(index, 1)} aria-label="Move stripe down"><ArrowDownIcon /></Button>
                <Button variant="ghost" size="icon" disabled={busy} onClick={() => setRemoveIndex(index)} aria-label="Remove stripe"><Trash2Icon className="text-red-300" /></Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Name"><Input value={output.name} maxLength={40} aria-invalid={errors.outputs?.[index]?.name ? true : undefined} onChange={(event) => updateOutput(index, { name: event.target.value })} />{errors.outputs?.[index]?.name && <InlineError message={errors.outputs[index]?.name?.message} />}</Field>
              <Field label="Pixel count"><Input type="number" min={1} max={4096} inputMode="numeric" value={output.pixels} aria-invalid={errors.outputs?.[index]?.pixels ? true : undefined} onChange={(event) => {
              const pixels = Number(event.target.value)
              updateOutput(index, {
                pixels,
                ...(pixels >= 1 ? { zone_count: Math.min(output.zone_count ?? 1, pixels) } : {}),
              })
            }} />{errors.outputs?.[index]?.pixels && <InlineError message={errors.outputs[index]?.pixels?.message} />}</Field>
            </div>
            <details className="group overflow-hidden rounded-xl border border-border/70 bg-muted/20">
              <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
                Zones, wiring, calibration and power
                <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
              </summary>
              <div className="space-y-4 border-t border-border/70 p-3 sm:p-4">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Zones per stripe">
                <Input
                  type="number"
                  min={1}
                  max={output.pixels}
                  inputMode="numeric"
                  value={output.zone_count ?? 1}
                  aria-invalid={errors.outputs?.[index]?.zone_count ? true : undefined}
                  onChange={(event) => updateOutput(index, { zone_count: Number(event.target.value) })}
                />
                <InlineError message={errors.outputs?.[index]?.zone_count?.message} />
              </Field>
              <Field label="Zone direction">
                <ToggleGroup
                  aria-label={`Zone direction for ${output.name}`}
                  value={[output.zone_pattern ?? "repeat"]}
                  onValueChange={(values) => {
                    const value = values[0]
                    if (value === "repeat" || value === "alternate") updateOutput(index, { zone_pattern: value })
                  }}
                  className="grid h-10 grid-cols-2 gap-1 rounded-lg bg-black/20 p-1"
                  variant="outline"
                >
                  <ToggleGroupItem value="repeat" className="h-full px-2 text-xs">Repeat</ToggleGroupItem>
                  <ToggleGroupItem value="alternate" className="h-full px-2 text-xs">Alternate</ToggleGroupItem>
                </ToggleGroup>
              </Field>
            </div>
            {(() => {
              const ranges = zoneRanges(output.pixels, output.zone_count ?? 1)
              return <div className="space-y-2 rounded-lg border border-white/10 bg-black/20 p-3">
                <div className="grid h-9 gap-px overflow-hidden rounded" style={{ gridTemplateColumns: `repeat(${ranges.length}, minmax(0, 1fr))` }} role="img" aria-label={`${ranges.length} animation zones across ${output.pixels} LEDs`}>
                  {ranges.map((range) => <span key={range.index} className="flex min-w-0 items-center justify-center border-r border-white/50 bg-violet-400/20 text-[9px] text-violet-100 last:border-r-0" title={`Zone ${range.index}: LEDs ${range.firstPixel}–${range.lastPixel}`}>
                    {ranges.length <= 12 ? `Z${range.index}` : ""}
                  </span>)}
                </div>
                <details className="text-xs text-muted-foreground">
                  <summary className="cursor-pointer">Show LED ranges for {ranges.length} zones</summary>
                  <ol className="mt-2 grid max-h-36 grid-cols-2 gap-x-4 gap-y-1 overflow-y-auto sm:grid-cols-3">
                    {ranges.map((range) => <li key={range.index}>Zone {range.index}: LEDs {range.firstPixel}–{range.lastPixel}</li>)}
                  </ol>
                </details>
              </div>
            })()}
            <div className="grid grid-cols-2 gap-3">
              <Field label="Voltage (V)"><Input type="number" min={0.1} step={0.1} inputMode="decimal" value={output.voltage_v} aria-invalid={errors.outputs?.[index]?.voltage_v ? true : undefined} onChange={(event) => updateOutput(index, { voltage_v: Number(event.target.value) })} />{errors.outputs?.[index]?.voltage_v && <InlineError message={errors.outputs[index]?.voltage_v?.message} />}</Field>
              <Field label="Full-white current / pixel (mA)"><Input type="number" min={0.1} step={0.1} inputMode="decimal" value={output.full_white_current_a * 1000} aria-invalid={errors.outputs?.[index]?.full_white_current_a ? true : undefined} onChange={(event) => updateOutput(index, { full_white_current_a: Number(event.target.value) / 1000 })} />{errors.outputs?.[index]?.full_white_current_a && <InlineError message={errors.outputs[index]?.full_white_current_a?.message} />}</Field>
            </div>
            <Field label="Optional output limit (watts)"><Input type="number" min={0.1} step={0.1} inputMode="decimal" placeholder="No separate limit" value={output.power_limit_watts ?? ""} aria-invalid={errors.outputs?.[index]?.power_limit_watts ? true : undefined} onChange={(event) => updateOutput(index, { power_limit_watts: event.target.value === "" ? null : Number(event.target.value) })} />{errors.outputs?.[index]?.power_limit_watts && <InlineError message={errors.outputs[index]?.power_limit_watts?.message} />}</Field>
            <div className="grid grid-cols-2 gap-2">
              {(["spi", "gpio"] as StripeBackend[]).map((backend) => <Button key={backend} type="button" variant={output.backend === backend ? "default" : "outline"} className="h-11 uppercase" onClick={() => updateOutput(index, { backend })}>{backend}</Button>)}
            </div>
            {output.backend === "spi" ? (
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="SPI device"><Input value={output.spi_device} onChange={(event) => updateOutput(index, { spi_device: event.target.value })} /></Field>
                <Field label="SPI speed (Hz)"><Input type="number" min={1} max={32000000} inputMode="numeric" value={output.spi_speed_hz} onChange={(event) => updateOutput(index, { spi_speed_hz: Number(event.target.value) })} /></Field>
              </div>
            ) : (
              <div className="space-y-3">
                <Field label="GPIO chip"><Input value={output.chip} onChange={(event) => updateOutput(index, { chip: event.target.value })} /></Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Data pin"><Input type="number" min={0} inputMode="numeric" value={output.data_pin} onChange={(event) => updateOutput(index, { data_pin: Number(event.target.value) })} /></Field>
                  <Field label="Clock pin"><Input type="number" min={0} inputMode="numeric" value={output.clock_pin} onChange={(event) => updateOutput(index, { clock_pin: Number(event.target.value) })} /></Field>
                </div>
              </div>
            )}
            <Toggle
              pressed={output.reversed}
              onPressedChange={(pressed) => updateOutput(index, { reversed: pressed })}
              variant="outline"
              aria-label={`Reverse direction ${output.reversed ? "on" : "off"}`}
              className="h-11 w-full justify-between px-3 data-pressed:border-violet-300/30 data-pressed:bg-violet-400/15 data-pressed:text-violet-100"
            >
              <span>Reverse direction</span>
              <span className="text-xs text-muted-foreground group-data-pressed/toggle:text-violet-200">{output.reversed ? "On" : "Off"}</span>
            </Toggle>
            <div className="grid grid-cols-5 gap-2">
              {(["identify", "red", "green", "blue", "white"] as const).map((pattern) => <Button key={pattern} variant="outline" className="h-10 px-1 text-xs capitalize" disabled={busy} aria-label={`Test ${pattern}`} onClick={() => void controller.testStripe(output.id, pattern, draft)}>{pattern === "identify" ? <ZapIcon /> : pattern}</Button>)}
            </div>
            {isDirty && <p className="text-xs text-amber-300">Tests use this draft temporarily, then restore the applied setup.</p>}
              </div>
            </details>
          </CardContent>
        </Card>
      })}
      </div>

      {fields.length < 2 && <Button variant="outline" className="h-12 w-full rounded-xl" disabled={busy} onClick={() => append(newStripe(draft.outputs))}><PlusIcon />Add stripe</Button>}
      <Button className="h-14 w-full rounded-2xl" disabled={!isDirty || busy} onClick={() => void handleSubmit(apply)()}><SaveIcon />{busy ? "Applying…" : "Save & apply"}</Button>
      {state.runtime !== "hardware" && <div className="rounded-xl border border-cyan-400/15 bg-cyan-400/5 p-3 text-xs text-cyan-100"><CableIcon className="mr-2 inline size-4" />Simulation mode: settings are saved, but physical devices are not opened.</div>}
      <AlertDialog open={removeIndex !== null} onOpenChange={(open) => { if (!open) setRemoveIndex(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this stripe?</AlertDialogTitle>
            <AlertDialogDescription>{removeIndex === null ? "" : `${draft.outputs[removeIndex]?.name ?? "This stripe"} will be turned off when you apply the configuration.`}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep stripe</AlertDialogCancel>
            <AlertDialogAction className="bg-red-500 text-white hover:bg-red-600" onClick={() => { if (removeIndex !== null) remove(removeIndex) }}>Remove</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SetupPage>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="grid gap-1.5 text-sm"><span className="text-xs font-medium text-muted-foreground">{label}</span>{children}</label>
}

function InlineError({ message }: { message?: string }) {
  return message ? <span role="alert" className="text-xs text-red-300">{message}</span> : null
}
