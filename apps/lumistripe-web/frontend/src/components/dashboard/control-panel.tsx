import { useState } from "react"
import { ActivityIcon, ListRestartIcon, Music2Icon, PaletteIcon, PowerIcon, RadioIcon, SunMediumIcon } from "lucide-react"

import { AnimationSheet } from "@/components/dashboard/animation-sheet"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Slider } from "@/components/ui/slider"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { DashboardController } from "@/hooks/use-dashboard"
import type { PlaybackMode } from "@/lib/api"

const modes: Array<{ value: PlaybackMode; label: string; icon: typeof RadioIcon }> = [
  { value: "solid", label: "Solid", icon: PaletteIcon },
  { value: "static", label: "Static", icon: RadioIcon },
  { value: "cycling", label: "Cycle", icon: ListRestartIcon },
  { value: "dynamic", label: "Music", icon: Music2Icon },
]

const solidPresets = ["#EF4444", "#F59E0B", "#22C55E", "#06B6D4", "#3B82F6", "#8B5CF6"]

export function ControlPanel({ controller }: { controller: DashboardController }) {
  const { state, animations, pendingCommand } = controller
  const [brightnessDraft, setBrightnessDraft] = useState<number | null>(null)
  const [colorDraft, setColorDraft] = useState<string | null>(null)

  if (!state) return null
  const disabled = !state.running || pendingCommand !== null
  const brightness = brightnessDraft ?? Math.round(state.brightness * 100)

  const changeMode = (values: string[]) => {
    const nextMode = values[0] as PlaybackMode | undefined
    if (nextMode && nextMode !== state.mode) void controller.setMode(nextMode)
  }

  const brightnessValue = (value: number | readonly number[]) =>
    Array.isArray(value) ? value[0] : value

  const changeBrightness = (value: number | readonly number[]) => {
    setBrightnessDraft(brightnessValue(value))
  }

  const commitBrightness = async (value: number | readonly number[]) => {
    await controller.setBrightness(brightnessValue(value) / 100)
    setBrightnessDraft(null)
  }

  const commitSolidColor = async (color: string) => {
    setColorDraft(color)
    await controller.setSolidColor(color)
    setColorDraft(null)
  }

  return (
    <div className="space-y-4 pb-4">
      <Button
        variant={state.blackout ? "default" : "destructive"}
        size="lg"
        className={state.blackout ? "h-14 w-full rounded-2xl bg-emerald-400 text-emerald-950 hover:bg-emerald-300" : "h-14 w-full rounded-2xl border-red-400/20 bg-red-500/15 text-red-200 hover:bg-red-500/25"}
        disabled={disabled}
        onClick={() => void controller.setBlackout(!state.blackout)}
      >
        <PowerIcon aria-hidden="true" />
        {state.blackout ? "Restore lights" : "Blackout"}
      </Button>

      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <SunMediumIcon className="size-4 text-amber-300" aria-hidden="true" />
            Brightness
          </CardTitle>
          <CardDescription>Set the overall output level.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex items-end justify-between">
            <span className="text-4xl font-semibold tracking-tight tabular-nums">{brightness}%</span>
            <span className="text-xs text-muted-foreground">Output</span>
          </div>
          <Slider
            aria-label="Brightness"
            min={0}
            max={100}
            step={1}
            value={[brightness]}
            disabled={disabled}
            onValueChange={changeBrightness}
            onValueCommitted={(value) => void commitBrightness(value)}
            className="py-4 [&_[data-slot=slider-track]]:h-2 [&_[data-slot=slider-range]]:bg-gradient-to-r [&_[data-slot=slider-range]]:from-violet-500 [&_[data-slot=slider-range]]:to-fuchsia-400 [&_[data-slot=slider-thumb]]:size-5 [&_[data-slot=slider-thumb]]:border-2 [&_[data-slot=slider-thumb]]:border-violet-300"
          />
        </CardContent>
      </Card>

      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ActivityIcon className="size-4 text-cyan-300" aria-hidden="true" />
            Playback
          </CardTitle>
          <CardDescription>Choose how LumiStripe changes over time.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ToggleGroup
            value={[state.mode]}
            onValueChange={changeMode}
            disabled={disabled}
            variant="outline"
            spacing={1}
            className="grid w-full grid-cols-4 rounded-xl bg-black/20 p-1"
            aria-label="Playback mode"
          >
            {modes.map(({ value, label, icon: Icon }) => (
              <ToggleGroupItem
                key={value}
                value={value}
                className="h-12 min-w-0 flex-col gap-0.5 rounded-lg border-0 px-1 text-xs data-pressed:bg-violet-400/15 data-pressed:text-violet-200 aria-pressed:bg-violet-400/15 aria-pressed:text-violet-200"
              >
                <Icon aria-hidden="true" />
                {label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>

          {state.mode === "solid" ? (
            <div className="space-y-3 rounded-xl border border-white/5 bg-black/20 p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">Solid color</div>
                  <div className="text-xs text-muted-foreground">Tap the swatch to choose a color.</div>
                </div>
                <label
                  className="relative size-12 shrink-0 cursor-pointer overflow-hidden rounded-xl border-2 border-white/15 shadow-lg"
                  style={{ backgroundColor: colorDraft ?? state.solid_color }}
                >
                  <span className="sr-only">Choose solid color</span>
                  <input
                    type="color"
                    aria-label="Choose solid color"
                    value={colorDraft ?? state.solid_color}
                    disabled={disabled}
                    onChange={(event) => void commitSolidColor(event.target.value)}
                    className="absolute inset-0 size-full cursor-pointer opacity-0"
                  />
                </label>
              </div>
              <div className="grid grid-cols-6 gap-2" aria-label="Solid color presets">
                {solidPresets.map((color) => (
                  <button
                    key={color}
                    type="button"
                    aria-label={`Set solid color ${color}`}
                    disabled={disabled}
                    onClick={() => void commitSolidColor(color)}
                    className="aspect-square rounded-full border-2 border-white/15 shadow-sm transition-transform active:scale-90 disabled:opacity-50"
                    style={{ backgroundColor: color }}
                  />
                ))}
              </div>
            </div>
          ) : (
            <AnimationSheet
              animations={animations}
              current={state.animation}
              disabled={disabled}
              onSelect={controller.selectAnimation}
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
