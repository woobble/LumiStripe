import { lazy, Suspense, useState, type ComponentProps } from "react"
import { Link } from "react-router"
import { ActivityIcon, ListRestartIcon, Music2Icon, PaletteIcon, PowerIcon, RadioIcon, Settings2Icon, SunMediumIcon } from "lucide-react"

import { AnimationSheet } from "@/components/dashboard/animation-sheet"
import { ColorControl } from "@/components/dashboard/color-control"
import { StripeTargetSelector } from "@/components/dashboard/stripe-target-selector"
import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import { Toggle } from "@/components/ui/toggle"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { PreviewStreamProvider } from "@/hooks/preview-stream-provider"
import { usePreviewStreamContext } from "@/hooks/preview-stream-context"
import type { DashboardController } from "@/hooks/use-dashboard"
import type { DashboardState, PlaybackMode } from "@/lib/api"

const LivePreview = lazy(async () => {
  const module = await import("@/components/dashboard/live-preview")
  return { default: module.LivePreview }
})

const modes: Array<{ value: PlaybackMode; label: string; accessibleLabel: string; icon: typeof RadioIcon }> = [
  { value: "solid", label: "Color", accessibleLabel: "Solid color", icon: PaletteIcon },
  { value: "static", label: "Static", accessibleLabel: "Static animation", icon: RadioIcon },
  { value: "cycling", label: "Cycle", accessibleLabel: "Cycling animations", icon: ListRestartIcon },
  { value: "dynamic", label: "Music", accessibleLabel: "Music reactive", icon: Music2Icon },
]

const firstSliderValue = (value: number | readonly number[]) =>
  typeof value === "number" ? value : value[0] ?? 100

function StreamedLivePreview({
  state,
  onDisable,
}: {
  state: DashboardState
  onDisable: () => void
}) {
  const { frame, connection } = usePreviewStreamContext()
  return (
    <LivePreview
      state={state}
      frame={frame}
      connection={connection}
      onDisable={onDisable}
      compact
    />
  )
}

type StreamedAnimationSheetProps = Omit<
  ComponentProps<typeof AnimationSheet>,
  "previewFrame" | "previewConnection"
>

function StreamedAnimationSheet(props: StreamedAnimationSheetProps) {
  const { frame, connection } = usePreviewStreamContext()
  return <AnimationSheet {...props} previewFrame={frame} previewConnection={connection} />
}

export function ControlPanel({
  controller,
  selectedStripe,
  onSelectedStripeChange,
}: {
  controller: DashboardController
  selectedStripe: string
  onSelectedStripeChange: (stripeId: string) => void
}) {
  const { state, animations, pendingCommand } = controller
  const [brightnessDraft, setBrightnessDraft] = useState<number | null>(null)
  const [previewEnabled, setPreviewEnabled] = useState(false)
  const [animationEditorVisible, setAnimationEditorVisible] = useState(false)

  if (!state) return null
  const disabled = !state.running || pendingCommand !== null
  const targetId = selectedStripe === "all" ? undefined : selectedStripe
  const targetPlayback = state.stripe_topology.layout === "independent"
    ? targetId
      ? state.stripe_playback.find((item) => item.stripe_id === targetId)
      : state.stripe_playback[0]
    : undefined
  const mode = targetPlayback?.mode ?? state.mode
  const solidColor = targetPlayback?.solid_color ?? state.solid_color
  const animation = targetPlayback?.animation ?? state.animation
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

  const changeBrightness = (value: number | readonly number[]) => {
    setBrightnessDraft(firstSliderValue(value))
  }

  const commitBrightness = async (value: number | readonly number[]) => {
    const ok = await controller.setBrightness(firstSliderValue(value) / 100, targetId)
    if (ok) setBrightnessDraft(null)
  }

  return (
    <PreviewStreamProvider enabled={Boolean(previewEnabled && state.running)}>
      <div className="space-y-5 pb-5">
        <header className="flex items-end justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">Lights</p>
            <h2 className="mt-1 text-2xl font-semibold tracking-tight">Control</h2>
          </div>
          <span className="inline-flex items-center gap-2 rounded-full border border-border/70 bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground" role="status" aria-live="polite">
            <span className={`size-2 rounded-full ${blackout || !state.running ? "bg-muted-foreground" : "bg-emerald-400"}`} aria-hidden="true" />
            {blackout ? "Lights off" : state.running ? "Lights on" : "Controller stopped"}
          </span>
        </header>

        <StripeTargetSelector
          state={state}
          value={selectedStripe}
          onChange={(value) => {
            setBrightnessDraft(null)
            onSelectedStripeChange(value)
          }}
        />

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(19rem,0.8fr)]">
          <div className="space-y-4">
            <section className="rounded-2xl border border-border/70 bg-card p-4 sm:p-5" aria-label="Power and brightness">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 className="font-medium">Brightness</h3>
                  <p className="mt-0.5 text-xs text-muted-foreground">Adjust the output for {selectedStripe === "all" ? "your lights" : state.stripe_topology.outputs.find((stripe) => stripe.id === selectedStripe)?.name ?? "this strip"}.</p>
                </div>
                <output className="text-3xl font-semibold tabular-nums tracking-tight" aria-label="Brightness level">{brightness}%</output>
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
                className="mt-3 py-3 [&_[data-slot=slider-track]]:h-2 [&_[data-slot=slider-thumb]]:size-5"
              />
              <div className="mt-2 flex items-center justify-between gap-3 border-t border-border/60 pt-3">
                <span className="text-xs text-muted-foreground" role="status" aria-live="polite">
                  {pendingCommand ? "Applying change…" : blackout ? "Lights are off" : "Output level"}
                </span>
                <Toggle
                  pressed={blackout}
                  variant="outline"
                  disabled={!state.running || pendingCommand !== null}
                  aria-label={blackout ? "Turn lights on" : "Turn lights off"}
                  onPressedChange={(pressed) => void controller.setBlackout(pressed, targetId)}
                  className={blackout
                    ? "h-10 rounded-xl border-border px-4"
                    : "h-10 rounded-xl border-emerald-300/30 bg-emerald-400 px-4 text-emerald-950 hover:bg-emerald-300 data-pressed:bg-emerald-300"}
                >
                  <PowerIcon aria-hidden="true" />
                  {blackout ? "Turn on" : "Turn off"}
                </Toggle>
              </div>
            </section>

            <section className="space-y-3 rounded-2xl border border-border/70 bg-card p-4 sm:p-5" aria-labelledby="playback-mode-heading">
              <div className="flex items-center justify-between gap-3">
                <h3 id="playback-mode-heading" className="font-medium">Playback mode</h3>
                <Link to="/playback" className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <Settings2Icon className="size-4" aria-hidden="true" />
                  More settings
                </Link>
              </div>
              <ToggleGroup
                value={[mode]}
                onValueChange={changeMode}
                disabled={disabled || pendingCommand === "mode"}
                variant="outline"
                spacing={1}
                className="grid w-full grid-cols-2 gap-2 sm:grid-cols-4"
                aria-label="Playback mode"
              >
                {modes.map(({ value, label, accessibleLabel, icon: Icon }) => (
                  <ToggleGroupItem
                    key={value}
                    value={value}
                    aria-label={accessibleLabel}
                    title={accessibleLabel}
                    className="h-12 min-w-0 flex-col gap-1 rounded-xl border border-border/70 px-1 text-xs text-muted-foreground data-pressed:border-primary/40 data-pressed:bg-primary/10 data-pressed:text-foreground aria-pressed:border-primary/40 aria-pressed:bg-primary/10 aria-pressed:text-foreground"
                  >
                    <Icon aria-hidden="true" />
                    {label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>

              {mode === "solid" ? (
                <ColorControl
                  key={targetId ?? "all"}
                  label="Solid color"
                  color={solidColor}
                  disabled={disabled || pendingCommand === "solidColor"}
                  onCommit={(color) => controller.setSolidColor(color, targetId)}
                />
              ) : (
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    <PaletteIcon className="size-4" aria-hidden="true" />
                    Current animation
                  </div>
                  <StreamedAnimationSheet
                    animations={animations}
                    current={animation}
                    disabled={disabled || pendingCommand === "animation"}
                    stripeId={targetId}
                    settingsDisabledReason={state.stripe_topology.layout === "independent" && targetId === undefined
                      ? "Choose a stripe to edit its animation settings."
                      : undefined}
                    onSelect={(name) => controller.selectAnimation(name, targetId)}
                    onLoadSettings={controller.getAnimationSettings}
                    onSaveSettings={controller.setAnimationSettings}
                    previewEnabled={previewEnabled}
                    previewTopology={state.stripe_topology}
                    onEnablePreview={() => setPreviewEnabled(true)}
                    onDisablePreview={() => setPreviewEnabled(false)}
                    onEditorVisibilityChange={setAnimationEditorVisible}
                  />
                </div>
              )}
            </section>
          </div>

          <aside className="space-y-4" aria-label="Live view and shortcuts">
            {previewEnabled ? animationEditorVisible ? (
              <div className="rounded-2xl border border-border/70 bg-card p-4 text-sm text-muted-foreground" role="status">
                Live preview is shown in the animation editor while it is open.
              </div>
            ) : (
              <Suspense fallback={<div className="h-40 animate-pulse rounded-2xl border border-border/70 bg-card" aria-label="Loading live preview" />}>
                <StreamedLivePreview state={state} onDisable={() => setPreviewEnabled(false)} />
              </Suspense>
            ) : (
              <section className="flex min-h-36 flex-col justify-between gap-4 rounded-2xl border border-border/70 bg-card p-4 sm:p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2 font-medium">
                      <ActivityIcon className="size-4 text-cyan-300" aria-hidden="true" />
                      Live preview
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">See the output from your configured strips.</p>
                  </div>
                  <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">Off</span>
                </div>
                <Button
                  variant="outline"
                  className="h-10 w-full justify-between rounded-xl border-border/70 px-3"
                  onClick={() => setPreviewEnabled(true)}
                  disabled={!state.running}
                  aria-label="Enable live preview"
                >
                  <span>{state.running ? "Enable live preview" : "Start the controller to preview"}</span>
                  <ActivityIcon className="size-4" aria-hidden="true" />
                </Button>
              </section>
            )}

            <section className="rounded-2xl border border-border/70 bg-card p-4 sm:p-5">
              <h3 className="font-medium">More controls</h3>
              <div className="mt-3 divide-y divide-border/70">
                <Link to="/playback" className="flex min-h-12 items-center justify-between gap-3 py-2 text-sm hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <span>Transitions and sequences</span>
                  <Settings2Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                </Link>
                <Link to="/setup/stripes" className="flex min-h-12 items-center justify-between gap-3 py-2 text-sm hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <span>Manage LED strips</span>
                  <Settings2Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                </Link>
              </div>
            </section>

            <div className="hidden items-center gap-2 px-1 text-xs text-muted-foreground xl:flex">
              <SunMediumIcon className="size-4" aria-hidden="true" />
              {state.stripe_topology.outputs.length} configured {state.stripe_topology.outputs.length === 1 ? "strip" : "strips"}
            </div>
          </aside>
        </div>
      </div>
    </PreviewStreamProvider>
  )
}
