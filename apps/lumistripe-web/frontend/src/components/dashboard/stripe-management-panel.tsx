import { useEffect, useRef, useState, type ReactNode } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { ArrowDownIcon, ArrowUpIcon, CableIcon, PlusIcon, SaveIcon, Trash2Icon, ZapIcon } from "lucide-react"
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
  outputs: z.array(z.object({
    id: z.string().min(1),
    name: z.string().trim().min(1, "Every stripe needs a name.").max(40),
    pixels: z.number().int().min(1).max(4096),
  }).passthrough()).min(1).max(2),
})

function copyTopology(topology: StripeTopology): StripeTopology {
  return { layout: topology.layout, outputs: topology.outputs.map((output) => ({ ...output })) }
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
    spi_device: existing.length === 0 ? "/dev/spidev0.0" : "/dev/spidev1.0",
    spi_speed_hz: 1_000_000,
    chip: "/dev/gpiochip0",
    data_pin: existing.length === 0 ? 10 : 20,
    clock_pin: existing.length === 0 ? 11 : 21,
  }
}

export function StripeManagementPanel({ controller }: { controller: DashboardController }) {
  const { state, pendingCommand } = controller
  const form = useForm<StripeTopology>({ resolver: zodResolver(stripeTopologySchema) as never, defaultValues: { layout: "mirrored", outputs: [] } })
  const { control, watch, setValue, reset, handleSubmit, formState: { isDirty, errors } } = form
  const { fields, append, remove: removeField, move: moveField } = useFieldArray({ control, name: "outputs" })
  const draft = watch()
  useUnsavedChangesGuard(isDirty)
  const [removeIndex, setRemoveIndex] = useState<number | null>(null)
  const syncedTopologyRef = useRef<string | null>(null)

  useEffect(() => {
    if (!state || isDirty) return
    const topologyKey = JSON.stringify(state.stripe_topology)
    if (topologyKey === syncedTopologyRef.current) return
    reset(copyTopology(state.stripe_topology))
    syncedTopologyRef.current = topologyKey
  }, [isDirty, reset, state?.stripe_topology])

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

  return (
    <SetupPage>
      <Card className="border-white/5 bg-card/80">
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

      {fields.map((field, index) => {
        const output = draft.outputs[index]
        if (!output) return null
        return <Card key={field.id} className="border-white/5 bg-card/80">
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
            <Field label="Name"><Input value={output.name} maxLength={40} aria-invalid={errors.outputs?.[index]?.name ? true : undefined} onChange={(event) => updateOutput(index, { name: event.target.value })} />{errors.outputs?.[index]?.name && <InlineError message={errors.outputs[index]?.name?.message} />}</Field>
            <Field label="Pixel count"><Input type="number" min={1} max={4096} inputMode="numeric" value={output.pixels} aria-invalid={errors.outputs?.[index]?.pixels ? true : undefined} onChange={(event) => updateOutput(index, { pixels: Number(event.target.value) })} />{errors.outputs?.[index]?.pixels && <InlineError message={errors.outputs[index]?.pixels?.message} />}</Field>
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
          </CardContent>
        </Card>
      })}

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
