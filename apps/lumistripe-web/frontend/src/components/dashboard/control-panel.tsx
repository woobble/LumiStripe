import { lazy, memo, Suspense, useState } from "react"
import { ActivityIcon, ListRestartIcon, Music2Icon, PaletteIcon, PowerIcon, RadioIcon, SunMediumIcon } from "lucide-react"
import { HexColorPicker } from "react-colorful"

import { AnimationSheet } from "@/components/dashboard/animation-sheet"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Slider } from "@/components/ui/slider"
import { Toggle } from "@/components/ui/toggle"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import type { DashboardController } from "@/hooks/use-dashboard"
import type { PlaybackMode } from "@/lib/api"

const LivePreview = lazy(async () => {
  const module = await import("@/components/dashboard/live-preview")
  return { default: module.LivePreview }
})

const modes: Array<{ value: PlaybackMode; label: string; icon: typeof RadioIcon }> = [
  { value: "solid", label: "Solid", icon: PaletteIcon },
  { value: "static", label: "Static", icon: RadioIcon },
  { value: "cycling", label: "Cycle", icon: ListRestartIcon },
  { value: "dynamic", label: "Music", icon: Music2Icon },
]

const solidPresets = ["#EF4444", "#F59E0B", "#22C55E", "#06B6D4", "#3B82F6", "#8B5CF6"]

type SolidModeSectionProps = {
  controller: DashboardController
  targetId?: string
  color: string
  disabled: boolean
}

const SolidModeSection = memo(function SolidModeSection({ controller, targetId, color, disabled }: SolidModeSectionProps) {
  const [colorDraft, setColorDraft] = useState<string | null>(null)
  const [colorOpen, setColorOpen] = useState(false)

  const commit = async (nextColor: string) => {
    setColorDraft(nextColor)
    const ok = await controller.setSolidColor(nextColor, targetId)
    if (ok) setColorDraft(null)
    return ok
  }

  return (
    <div className="space-y-3 rounded-xl border border-white/5 bg-black/20 p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium">Solid color</div>
          <div className="text-xs text-muted-foreground">Tap the swatch to choose a color.</div>
        </div>
        <Popover open={colorOpen} onOpenChange={setColorOpen}>
          <PopoverTrigger disabled={disabled} aria-label="Choose solid color" className="size-12 shrink-0 rounded-xl border-2 border-white/15 shadow-lg" style={{ backgroundColor: colorDraft ?? color }} />
          <PopoverContent className="w-64">
            <HexColorPicker color={colorDraft ?? color} onChange={setColorDraft} />
            <Button className="w-full" disabled={disabled} onClick={() => void commit(colorDraft ?? color).then((ok) => { if (ok) setColorOpen(false) })}>Apply color</Button>
          </PopoverContent>
        </Popover>
      </div>
      <div className="grid grid-cols-6 gap-2" aria-label="Solid color presets">
        {solidPresets.map((preset) => (
          <button
            key={preset}
            type="button"
            aria-label={`Set solid color ${preset}`}
            disabled={disabled}
            onClick={() => void commit(preset)}
            className="aspect-square cursor-pointer rounded-full border-2 border-white/15 shadow-sm transition-transform active:scale-90 disabled:cursor-not-allowed disabled:opacity-50"
            style={{ backgroundColor: preset }}
          />
        ))}
      </div>
    </div>
  )
}, (previous, next) => (
  previous.targetId === next.targetId
  && previous.color === next.color
  && previous.disabled === next.disabled
))

export function ControlPanel({ controller }: { controller: DashboardController }) {
  const { state, animations, pendingCommand } = controller
  const [brightnessDraft, setBrightnessDraft] = useState<number | null>(null)
  const [target, setTarget] = useState("all")
  const [previewEnabled, setPreviewEnabled] = useState(false)

  if (!state) return null
  const disabled = !state.running || pendingCommand !== null
  const targetId = target === "all" ? undefined : target
  const targetPlayback = state.stripe_topology.layout === "independent"
    ? (targetId
        ? state.stripe_playback.find((item) => item.stripe_id === targetId)
        : state.stripe_playback[0])
    : undefined
  const mode = targetPlayback?.mode ?? state.mode
  const solidColor = targetPlayback?.solid_color ?? state.solid_color
  const animation = targetPlayback?.animation ?? state.animation
  const musicRecognitionEnabled = targetPlayback?.music_recognition_enabled ?? state.music_recognition_enabled ?? true
  const blackout = targetId
    ? (targetPlayback?.blackout ?? false)
    : state.stripe_topology.layout === "independent" && state.stripe_playback.length > 0
      ? state.stripe_playback.every((item) => item.blackout)
      : state.blackout
  const brightness = brightnessDraft ?? Math.round((targetPlayback?.brightness ?? state.brightness) * 100)

  const changeMode = (values: string[]) => {
    const nextMode = values[0] as PlaybackMode | undefined
    if (nextMode && nextMode !== mode) void controller.setMode(nextMode, targetId)
  }

  const changeMusicRecognition = (enabled: boolean) => {
    if (mode === "static" || mode === "cycling") {
      void controller.setMode(mode, targetId, enabled)
    }
  }

  const brightnessValue = (value: number | readonly number[]) =>
    Array.isArray(value) ? value[0] : value

  const changeBrightness = (value: number | readonly number[]) => {
    setBrightnessDraft(brightnessValue(value))
  }

  const commitBrightness = async (value: number | readonly number[]) => {
    const ok = await controller.setBrightness(brightnessValue(value) / 100, targetId)
    if (ok) setBrightnessDraft(null)
  }

  return (
    <div className="space-y-4 pb-4">
      {previewEnabled ? (
        <Suspense fallback={<div className="h-64 animate-pulse rounded-2xl border border-white/5 bg-card/80" aria-label="Loading live preview" />}>
          <LivePreview state={state} onDisable={() => setPreviewEnabled(false)} />
        </Suspense>
      ) : (
        <Button
          variant="outline"
          className="h-12 w-full justify-between rounded-2xl border-white/10 bg-card/50 px-4 shadow-lg shadow-black/5"
          onClick={() => setPreviewEnabled(true)}
          aria-label="Enable live preview"
        >
          <span className="flex items-center gap-2">
            <ActivityIcon className="size-4 text-cyan-300" aria-hidden="true" />
            Enable live preview
          </span>
          <span className="text-xs text-muted-foreground">Off by default</span>
        </Button>
      )}
      {state.stripe_topology.layout === "independent" && state.stripe_topology.outputs.length > 0 && (
        <section className="space-y-2" aria-labelledby="control-target-label">
          <div className="flex items-center justify-between px-1 text-xs">
            <span id="control-target-label" className="font-medium text-muted-foreground">Control target</span>
            <span className="max-w-[55%] truncate text-violet-200">
              {target === "all" ? "All stripes" : state.stripe_topology.outputs.find((stripe) => stripe.id === target)?.name}
            </span>
          </div>
          <ToggleGroup
            value={[target]}
            onValueChange={(values) => {
              if (!values[0]) return
              setBrightnessDraft(null)
              setTarget(values[0])
            }}
            variant="outline"
            spacing={1}
            className="grid w-full gap-1 rounded-xl border border-white/5 bg-black/20 p-1"
            style={{ gridTemplateColumns: `repeat(${state.stripe_topology.outputs.length + 1}, minmax(0, 1fr))` }}
            aria-label="Control target"
          >
            <ToggleGroupItem value="all" className="h-11 min-w-0 rounded-lg border-0 px-2 data-pressed:bg-violet-400/15 data-pressed:text-violet-100">All</ToggleGroupItem>
            {state.stripe_topology.outputs.map((stripe) => (
              <ToggleGroupItem
                key={stripe.id}
                value={stripe.id}
                title={stripe.name}
                className="h-11 min-w-0 truncate rounded-lg border-0 px-2 data-pressed:bg-violet-400/15 data-pressed:text-violet-100"
              >
                <span className="truncate">{stripe.name}</span>
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </section>
      )}
      <Toggle
        pressed={blackout}
        variant="outline"
        size="lg"
        className={blackout ? "h-14 w-full rounded-2xl border-red-300/30 bg-red-500/15 text-red-200 hover:bg-red-500/25 data-pressed:bg-red-500/20" : "h-14 w-full rounded-2xl border-emerald-300/30 bg-emerald-400 text-emerald-950 hover:bg-emerald-300 data-pressed:bg-emerald-300"}
        disabled={disabled || pendingCommand !== null}
        aria-label={blackout ? "Restore lights" : "Blackout"}
        onPressedChange={(pressed) => void controller.setBlackout(pressed, targetId)}
      >
        <PowerIcon aria-hidden="true" />
        {blackout ? "Restore lights" : "Blackout"}
      </Toggle>

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
            disabled={disabled || pendingCommand === "brightness"}
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
            value={[mode]}
            onValueChange={changeMode}
            disabled={disabled || pendingCommand === "mode"}
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

          {(mode === "static" || mode === "cycling") && (
            <div className="space-y-2 rounded-xl border border-white/5 bg-black/20 p-3">
              <Toggle
                pressed={musicRecognitionEnabled}
                onPressedChange={changeMusicRecognition}
                disabled={disabled || pendingCommand === "mode"}
                variant="outline"
                className="h-11 w-full justify-between rounded-lg px-3 data-[state=on]:border-violet-300/40 data-[state=on]:bg-violet-400/15 data-[state=on]:text-violet-100"
                aria-label="Music recognition"
              >
                <span>Music recognition</span>
                <span className="text-xs text-muted-foreground">{musicRecognitionEnabled ? "On" : "Off"}</span>
              </Toggle>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Let live audio influence this animation without switching to Music mode.
              </p>
            </div>
          )}

          {mode === "solid" ? (
            <SolidModeSection
              controller={controller}
              targetId={targetId}
              color={solidColor}
              disabled={disabled || pendingCommand === "solidColor"}
            />
          ) : (
            <AnimationSheet
              animations={animations}
              current={animation}
              disabled={disabled || pendingCommand === "animation"}
              onSelect={(name) => controller.selectAnimation(name, targetId)}
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
