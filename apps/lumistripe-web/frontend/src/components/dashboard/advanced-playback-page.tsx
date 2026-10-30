import { useState } from "react"
import { Link } from "react-router"
import { ArrowLeftIcon, ChevronRightIcon, Music2Icon, Settings2Icon } from "lucide-react"

import { AnimationSheet } from "@/components/dashboard/animation-sheet"
import { ColorControl } from "@/components/dashboard/color-control"
import { SequencerPanel } from "@/components/dashboard/sequencer-panel"
import { StripeTargetSelector } from "@/components/dashboard/stripe-target-selector"
import { TransitionSettingsPanel } from "@/components/dashboard/transition-settings-panel"
import { Slider } from "@/components/ui/slider"
import { Toggle } from "@/components/ui/toggle"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { PreviewStreamProvider } from "@/hooks/preview-stream-provider"
import { usePreviewStreamContext } from "@/hooks/preview-stream-context"
import type { DashboardController } from "@/hooks/use-dashboard"

const musicIdleDefault = { mode: "off" as const, color: "#2060FF", animation: "", brightness: 1 }

function StreamedAnimationSheet({
  controller,
  targetId,
  current,
  musicIdle,
}: {
  controller: DashboardController
  targetId: string | undefined
  current: string
  musicIdle: { mode: "off" | "solid" | "animation"; color: string; animation: string; brightness: number }
}) {
  const { frame, connection } = usePreviewStreamContext()
  return (
    <AnimationSheet
      selectionOnly
      animations={controller.animations}
      current={current}
      disabled={!controller.state?.running || controller.pendingCommand !== null}
      stripeId={targetId}
      onSelect={(name) => controller.setMusicIdle({ ...musicIdle, animation: name }, targetId)}
      onLoadSettings={controller.getAnimationSettings}
      onSaveSettings={controller.setAnimationSettings}
      previewEnabled={false}
      previewFrame={frame}
      previewConnection={connection}
      previewTopology={controller.state!.stripe_topology}
      onEnablePreview={() => {}}
      onDisablePreview={() => {}}
      onEditorVisibilityChange={() => {}}
    />
  )
}

export function AdvancedPlaybackPage({ controller }: { controller: DashboardController }) {
  const { state, pendingCommand } = controller
  const [target, setTarget] = useState("all")
  const [idleBrightnessDraft, setIdleBrightnessDraft] = useState<number | null>(null)
  if (!state) return null

  const targetId = target === "all" ? undefined : target
  const targetPlayback = state.stripe_topology.layout === "independent"
    ? targetId
      ? state.stripe_playback.find((item) => item.stripe_id === targetId)
      : state.stripe_playback[0]
    : undefined
  const mode = targetPlayback?.mode ?? state.mode
  const musicRecognitionEnabled = targetPlayback?.music_recognition_enabled ?? state.music_recognition_enabled ?? true
  const musicIdle = targetPlayback?.music_idle ?? state.music_idle ?? musicIdleDefault
  const disabled = !state.running || pendingCommand !== null
  const idleBrightness = idleBrightnessDraft ?? Math.round(musicIdle.brightness * 100)

  return (
    <PreviewStreamProvider enabled={false}>
      <div className="space-y-5 pb-5">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <Link to="/" className="mb-3 inline-flex min-h-9 items-center gap-1 rounded-lg px-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <ArrowLeftIcon className="size-4" aria-hidden="true" />
              Back to lights
            </Link>
            <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">Playback</p>
            <h2 className="mt-1 text-2xl font-semibold tracking-tight">Advanced settings</h2>
          </div>
          <span className="inline-flex items-center gap-2 rounded-full border border-border/70 bg-card px-3 py-1.5 text-xs text-muted-foreground">
            <Settings2Icon className="size-4" aria-hidden="true" />
            {mode === "dynamic" ? "Music reactive" : mode === "cycling" ? "Cycling" : mode === "solid" ? "Solid color" : "Static"}
          </span>
        </header>

        <StripeTargetSelector state={state} value={target} onChange={setTarget} />

        {(mode === "static" || mode === "cycling") && (
          <section className="rounded-2xl border border-border/70 bg-card p-4 sm:p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h3 className="font-medium">Music recognition</h3>
                <p className="mt-1 max-w-2xl text-sm text-muted-foreground">Let live audio influence this animation while keeping the selected playback mode.</p>
              </div>
              <Toggle
                pressed={musicRecognitionEnabled}
                onPressedChange={(enabled) => void controller.setMode(mode, targetId, enabled)}
                disabled={disabled || pendingCommand === "mode"}
                variant="outline"
                aria-label="Music recognition"
                className="h-10 min-w-24 rounded-xl"
              >
                {musicRecognitionEnabled ? "On" : "Off"}
              </Toggle>
            </div>
          </section>
        )}

        {mode === "dynamic" && (
          <section className="space-y-4 rounded-2xl border border-border/70 bg-card p-4 sm:p-5">
            <div className="flex items-start gap-3">
              <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
                <Music2Icon className="size-5" aria-hidden="true" />
              </div>
              <div>
                <h3 className="font-medium">When music is idle</h3>
                <p className="mt-1 text-sm text-muted-foreground">Choose what the strip shows when no music activity is detected.</p>
              </div>
            </div>

            <div className="space-y-3 rounded-xl border border-border/70 bg-muted/20 p-3 sm:p-4">
              <div className="flex items-end justify-between gap-3">
                <span className="text-sm font-medium">Idle brightness</span>
                <output className="text-sm tabular-nums text-muted-foreground">{idleBrightness}%</output>
              </div>
              <Slider
                aria-label="Idle brightness"
                min={0}
                max={100}
                step={1}
                value={[idleBrightness]}
                disabled={disabled || pendingCommand === "musicIdle"}
                onValueChange={(value) => setIdleBrightnessDraft(Array.isArray(value) ? value[0] ?? 100 : value)}
                onValueCommitted={(value) => {
                  const next = Array.isArray(value) ? value[0] ?? 100 : value
                  setIdleBrightnessDraft(next)
                  void controller.setMusicIdle({ ...musicIdle, brightness: next / 100 }, targetId).finally(() => setIdleBrightnessDraft(null))
                }}
              />
              <ToggleGroup
                aria-label="Music idle mode"
                value={[musicIdle.mode]}
                onValueChange={(values) => {
                  const next = values[0] as "off" | "solid" | "animation" | undefined
                  if (next && next !== musicIdle.mode) {
                    void controller.setMusicIdle({ ...musicIdle, mode: next, animation: musicIdle.animation || controller.animations[0]?.name || "" }, targetId)
                  }
                }}
                disabled={disabled || pendingCommand === "musicIdle"}
                className="grid w-full grid-cols-3 gap-2"
              >
                <ToggleGroupItem value="off" className="h-11 min-w-0 rounded-xl border border-border/70 px-2 text-xs data-pressed:border-primary/40 data-pressed:bg-primary/10">Off</ToggleGroupItem>
                <ToggleGroupItem value="solid" className="h-11 min-w-0 rounded-xl border border-border/70 px-2 text-xs data-pressed:border-primary/40 data-pressed:bg-primary/10">Solid color</ToggleGroupItem>
                <ToggleGroupItem value="animation" disabled={controller.animations.length === 0} className="h-11 min-w-0 rounded-xl border border-border/70 px-2 text-xs data-pressed:border-primary/40 data-pressed:bg-primary/10">Animation</ToggleGroupItem>
              </ToggleGroup>
              {musicIdle.mode === "solid" && (
                <ColorControl
                  key={targetId ?? "all"}
                  label="Idle color"
                  color={musicIdle.color}
                  disabled={disabled || pendingCommand === "musicIdle"}
                  onCommit={(color) => controller.setMusicIdle({ ...musicIdle, color }, targetId)}
                />
              )}
              {musicIdle.mode === "animation" && controller.animations.length > 0 && (
                <StreamedAnimationSheet controller={controller} targetId={targetId} current={musicIdle.animation} musicIdle={musicIdle} />
              )}
            </div>
          </section>
        )}

        <TransitionSettingsPanel controller={controller} disabled={disabled} />
        <SequencerPanel
          state={state}
          target={target}
          disabled={disabled}
          saving={pendingCommand === "sequencer"}
          sequenceBusy={pendingCommand !== null}
          sequenceSaving={pendingCommand === "sequence"}
          animations={controller.animations}
          animationsLoading={controller.animationsLoading}
          animationsError={controller.animationsError}
          sequences={controller.sequences}
          sequenceLoading={controller.sequenceLibraryLoading}
          sequenceLoadError={controller.sequenceLibraryError}
          sequenceActionError={controller.sequenceActionError}
          onApply={controller.setSequencerSettings}
          onRefreshSequences={controller.refreshSequences}
          onCreateSequence={controller.createSequence}
          onUpdateSequence={controller.updateSequence}
          onDeleteSequence={controller.deleteSequence}
          onSetActiveSequence={controller.setActiveSequence}
        />

        <Link to="/setup/audio/tuning" className="flex min-h-12 items-center justify-between gap-3 rounded-xl border border-border/70 bg-card px-4 text-sm hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <span>Audio tuning for reactive playback</span>
          <ChevronRightIcon className="size-4 text-muted-foreground" aria-hidden="true" />
        </Link>
      </div>
    </PreviewStreamProvider>
  )
}
