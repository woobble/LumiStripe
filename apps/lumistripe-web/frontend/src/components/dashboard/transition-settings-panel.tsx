import { useState } from "react"
import { ChevronDownIcon } from "lucide-react"

import { Slider } from "@/components/ui/slider"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Toggle } from "@/components/ui/toggle"
import type { DashboardController } from "@/hooks/use-dashboard"
import type { TransitionEffect } from "@/lib/api"

type TransitionSettingsPanelProps = {
  controller: DashboardController
  disabled: boolean
}

export function TransitionSettingsPanel({ controller, disabled }: TransitionSettingsPanelProps) {
  const settings = controller.state?.transition_settings ?? {
    enabled: true,
    duration_ms: 300,
    effect: "crossfade" as const,
  }
  const [durationDraft, setDurationDraft] = useState<number | null>(null)
  const duration = durationDraft ?? settings.duration_ms
  const effectLabel = settings.effect === "fade_black"
    ? "Fade through black"
    : settings.effect === "wipe"
      ? "Wipe"
      : "Crossfade"

  const apply = async (enabled: boolean, durationMs: number, effect: TransitionEffect) => {
    const applied = await controller.setTransitionSettings(enabled, durationMs, effect)
    if (applied) setDurationDraft(null)
  }

  const handleSliderChange = (value: number | readonly number[]) => {
    setDurationDraft(Array.isArray(value) ? value[0] ?? settings.duration_ms : value)
  }

  const handleSliderCommit = (value: number | readonly number[]) => {
    const durationMs = Array.isArray(value) ? value[0] ?? settings.duration_ms : value
    void apply(settings.enabled, durationMs, settings.effect)
  }

  return (
    <details className="group overflow-hidden rounded-2xl border border-border/70 bg-card">
      <summary className="flex min-h-16 cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-5">
        <span>
          <span className="block font-medium">Transitions</span>
          <span className="mt-0.5 block text-sm text-muted-foreground">
            {settings.enabled ? "On" : "Off"} · {effectLabel} · {duration} ms
          </span>
        </span>
        <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
      </summary>
      <div className="space-y-4 border-t border-border/70 p-4 sm:p-5">
        <Toggle
          pressed={settings.enabled}
          onPressedChange={(enabled) => void apply(enabled, settings.duration_ms, settings.effect)}
          disabled={disabled}
          variant="outline"
          className="h-11 w-full justify-between rounded-xl px-3 data-pressed:border-primary/40 data-pressed:bg-primary/10 data-pressed:text-foreground"
          aria-label="Animation transitions"
        >
          <span>Animation transitions</span>
          <span className="text-xs text-muted-foreground">{settings.enabled ? "On" : "Off"}</span>
        </Toggle>
        <div className="space-y-2">
          <label htmlFor="transition-effect" className="text-sm font-medium">Effect</label>
          <Select
            value={settings.effect}
            onValueChange={(value) => {
              if (value) void apply(settings.enabled, settings.duration_ms, value as TransitionEffect)
            }}
            disabled={disabled}
          >
            <SelectTrigger id="transition-effect" aria-label="Transition effect" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="crossfade">Crossfade</SelectItem>
              <SelectItem value="fade_black">Fade through black</SelectItem>
              <SelectItem value="wipe">Wipe</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <div className="flex items-end justify-between">
            <label htmlFor="transition-duration" className="text-sm font-medium">Duration</label>
            <output htmlFor="transition-duration" className="text-sm tabular-nums text-muted-foreground">
              {duration} ms
            </output>
          </div>
          <Slider
            id="transition-duration"
            aria-label="Transition duration"
            min={100}
            max={1500}
            step={50}
            value={[duration]}
            disabled={disabled || !settings.enabled}
            onValueChange={handleSliderChange}
            onValueCommitted={handleSliderCommit}
          />
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>Fast (100 ms)</span>
            <span>Slow (1500 ms)</span>
          </div>
        </div>
      </div>
    </details>
  )
}
