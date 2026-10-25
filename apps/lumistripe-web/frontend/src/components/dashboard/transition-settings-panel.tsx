import { useState } from "react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Slider } from "@/components/ui/slider"
import { Toggle } from "@/components/ui/toggle"
import type { DashboardController } from "@/hooks/use-dashboard"

type TransitionSettingsPanelProps = {
  controller: DashboardController
  disabled: boolean
}

export function TransitionSettingsPanel({ controller, disabled }: TransitionSettingsPanelProps) {
  const settings = controller.state?.transition_settings ?? { enabled: true, duration_ms: 300 }
  const [durationDraft, setDurationDraft] = useState<number | null>(null)
  const duration = durationDraft ?? settings.duration_ms

  const apply = async (enabled: boolean, durationMs: number) => {
    const applied = await controller.setTransitionSettings(enabled, durationMs)
    if (applied) setDurationDraft(null)
  }

  const handleSliderChange = (value: number | readonly number[]) => {
    setDurationDraft(Array.isArray(value) ? value[0] ?? settings.duration_ms : value)
  }

  const handleSliderCommit = (value: number | readonly number[]) => {
    const durationMs = Array.isArray(value) ? value[0] ?? settings.duration_ms : value
    void apply(settings.enabled, durationMs)
  }

  return (
    <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
      <CardHeader>
        <CardTitle>Transitions</CardTitle>
        <CardDescription>Crossfade between animations when they change.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Toggle
          pressed={settings.enabled}
          onPressedChange={(enabled) => void apply(enabled, settings.duration_ms)}
          disabled={disabled}
          variant="outline"
          className="h-11 w-full justify-between rounded-lg px-3 data-[state=on]:border-violet-300/40 data-[state=on]:bg-violet-400/15 data-[state=on]:text-violet-100"
          aria-label="Transitions"
        >
          <span>Animation transitions</span>
          <span className="text-xs text-muted-foreground">{settings.enabled ? "On" : "Off"}</span>
        </Toggle>
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
      </CardContent>
    </Card>
  )
}
