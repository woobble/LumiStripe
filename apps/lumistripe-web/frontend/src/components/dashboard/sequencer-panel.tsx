import { useState } from "react"
import { ChevronDownIcon, SaveIcon, SparklesIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { SequenceLibrary } from "@/components/dashboard/sequence-library"
import type {
  AnimationOption,
  AnimationSequenceContract,
  DashboardState,
  SequenceCreateRequest,
  SequenceDefinitionRequest,
  SequencerDiagnostics,
  SequencerSettingsResponse,
  SwitchQuantization,
} from "@/lib/api"

const defaultSettings: SequencerSettingsResponse = {
  switch_quantization: "off",
  bars_per_phrase: 8,
}

const defaultDiagnostics: SequencerDiagnostics = {
  mode: "off",
  beats_per_bar: 4,
  bars_per_phrase: 8,
  beat_index: null,
  beat_in_bar: null,
  bar_index: null,
  bar_in_phrase: null,
  phrase_index: null,
  beat_in_phrase: null,
  bpm: 0,
  beat_clock_confidence: 0,
  alignment_estimated: false,
  pending_animation: null,
  next_switch_in_s: null,
  waiting_for_clock: false,
}

const timingOptions: Array<{ value: SwitchQuantization; label: string }> = [
  { value: "off", label: "Off" },
  { value: "beat", label: "Beat" },
  { value: "bar", label: "Bar" },
  { value: "phrase", label: "Phrase" },
]

type SequencerPanelProps = {
  state: DashboardState
  target: string
  disabled: boolean
  saving: boolean
  sequenceBusy: boolean
  sequenceSaving: boolean
  animations: AnimationOption[]
  animationsLoading: boolean
  animationsError: string | null
  sequences: AnimationSequenceContract[]
  sequenceLoading: boolean
  sequenceLoadError: string | null
  sequenceActionError: string | null
  onApply: (mode: SwitchQuantization, barsPerPhrase: 4 | 8 | 16) => Promise<boolean>
  onRefreshSequences: () => Promise<void>
  onCreateSequence: (sequence: SequenceCreateRequest) => Promise<boolean>
  onUpdateSequence: (sequenceId: string, sequence: SequenceDefinitionRequest) => Promise<boolean>
  onDeleteSequence: (sequenceId: string) => Promise<boolean>
  onSetActiveSequence: (sequenceId: string | null) => Promise<boolean>
}

export function SequencerPanel({
  state,
  target,
  disabled,
  saving,
  sequenceBusy,
  sequenceSaving,
  animations,
  animationsLoading,
  animationsError,
  sequences,
  sequenceLoading,
  sequenceLoadError,
  sequenceActionError,
  onApply,
  onRefreshSequences,
  onCreateSequence,
  onUpdateSequence,
  onDeleteSequence,
  onSetActiveSequence,
}: SequencerPanelProps) {
  const settings = state.sequencer_settings ?? defaultSettings
  const [modeDraft, setModeDraft] = useState<SwitchQuantization | null>(null)
  const [barsDraft, setBarsDraft] = useState<4 | 8 | 16 | null>(null)
  const mode = modeDraft ?? settings.switch_quantization
  const bars = barsDraft ?? settings.bars_per_phrase
  const dirty = mode !== settings.switch_quantization || bars !== settings.bars_per_phrase
  const activeSequenceId = state.sequence_library?.active_sequence_id ?? null
  const activeSequence = sequences.find((sequence) => sequence.id === activeSequenceId)
  const timingSummary = mode === "off"
    ? "Free switching"
    : mode === "phrase"
      ? `Every ${bars}-bar phrase`
      : `Every ${mode}`
  const sequenceSummary = activeSequence?.name
    ?? (sequenceLoading ? "Loading sequences…" : activeSequenceId ? "Saved sequence selected" : "Automatic selection")

  const apply = async () => {
    if (await onApply(mode, bars)) {
      setModeDraft(null)
      setBarsDraft(null)
    }
  }

  const outputStates = state.stripe_playback
  const rows = state.stripe_topology.layout === "independent"
    ? target === "all"
      ? outputStates
      : outputStates.filter((output) => output.stripe_id === target)
    : outputStates.slice(0, 1)

  return (
    <Card className="border-border/70 bg-card shadow-none">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2">
          <SparklesIcon className="size-4 text-primary" aria-hidden="true" />
          Sequencer
        </CardTitle>
        <CardDescription>Choose when music-reactive playback changes animations.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2 rounded-xl border border-border/70 bg-muted/25 p-3 sm:grid-cols-2">
          <div>
            <div className="text-xs text-muted-foreground">Switch timing</div>
            <div className="mt-0.5 font-medium">{timingSummary}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">Selected sequence</div>
            <div className="mt-0.5 truncate font-medium" title={sequenceSummary}>{sequenceSummary}</div>
          </div>
        </div>

        <div className="space-y-2">
          <div className="text-sm font-medium">Animation switch timing</div>
          <ToggleGroup
            value={[mode]}
            onValueChange={(values) => {
              const selected = values[0] as SwitchQuantization | undefined
              if (selected) setModeDraft(selected)
            }}
            disabled={disabled || saving}
            variant="outline"
            spacing={1}
            className="grid w-full grid-cols-2 gap-1 sm:grid-cols-4"
            aria-label="Animation switch timing"
          >
            {timingOptions.map(({ value, label }) => (
              <ToggleGroupItem
                key={value}
                value={value}
                className="h-11 min-w-0 rounded-xl border border-border/70 px-2 text-xs data-pressed:border-primary/40 data-pressed:bg-primary/10 data-pressed:text-foreground"
              >
                {label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>

        {mode === "phrase" && (
          <div className="space-y-2">
            <div className="text-sm font-medium">Phrase length</div>
            <ToggleGroup
              value={[String(bars)]}
              onValueChange={(values) => {
                const selected = Number(values[0])
                if (selected === 4 || selected === 8 || selected === 16) {
                  setBarsDraft(selected)
                }
              }}
              disabled={disabled || saving}
              variant="outline"
              spacing={1}
              className="grid w-full grid-cols-3 gap-1"
              aria-label="Bars per phrase"
            >
              {([4, 8, 16] as const).map((value) => (
                <ToggleGroupItem
                  key={value}
                  value={String(value)}
                  className="h-11 rounded-xl border border-border/70 px-2 text-xs data-pressed:border-primary/40 data-pressed:bg-primary/10 data-pressed:text-foreground"
                >
                  {value} bars
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        )}

        <Button
          className="min-h-11 w-full rounded-xl"
          disabled={disabled || saving || !dirty}
          onClick={() => void apply()}
          aria-label="Save sequencer timing"
        >
          <SaveIcon aria-hidden="true" />
          {saving ? "Saving…" : "Save timing"}
        </Button>

        <div className="border-t border-white/5 pt-4">
          <SequenceLibrary
            state={state}
            target={target}
            animations={animations}
            animationLoading={animationsLoading}
            animationLoadError={animationsError}
            sequences={sequences}
            loading={sequenceLoading}
            loadError={sequenceLoadError}
            actionError={sequenceActionError}
            busy={sequenceBusy}
            saving={sequenceSaving}
            onRefresh={onRefreshSequences}
            onCreate={onCreateSequence}
            onUpdate={onUpdateSequence}
            onDelete={onDeleteSequence}
            onSetActive={onSetActiveSequence}
          />
        </div>

        <details className="group rounded-xl border border-border/70">
          <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
            <span>Beat timing details</span>
            <ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
          </summary>
          <div className="space-y-3 border-t border-border/70 p-3" aria-label="Sequencer diagnostics">
            {rows.length > 0 ? rows.map((output) => {
              const diagnostics = output.sequencer_diagnostics ?? defaultDiagnostics
              const name = state.stripe_topology.layout === "independent"
                ? state.stripe_topology.outputs.find((stripe) => stripe.id === output.stripe_id)?.name ?? output.stripe_id
                : null
              return (
                <section key={output.stripe_id} className="space-y-2 rounded-lg bg-black/20 p-3" aria-label={name ? `${name} sequencer status` : "Sequencer status"}>
                  {name && <div className="text-xs font-medium text-foreground">{name}</div>}
                  <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs sm:grid-cols-4">
                    <div>
                      <div className="text-muted-foreground">Current</div>
                      <div className="truncate font-medium" title={output.animation}>{output.animation || "—"}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">Pending</div>
                      <div className="truncate font-medium" title={diagnostics.pending_animation ?? undefined}>
                        {diagnostics.pending_animation ?? (diagnostics.waiting_for_clock ? "Waiting for clock" : "None")}
                      </div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">Beat in bar</div>
                      <div className="font-medium tabular-nums">
                        {diagnostics.beat_in_bar ?? "—"} / {diagnostics.beats_per_bar}
                      </div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">Phrase</div>
                      <div className="font-medium tabular-nums">
                        {diagnostics.phrase_index ?? "—"} · bar {diagnostics.bar_in_phrase ?? "—"} / {diagnostics.bars_per_phrase}
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
                    <span>Clock confidence {Math.round(diagnostics.beat_clock_confidence * 100)}%</span>
                    <span>
                      {diagnostics.pending_animation && diagnostics.next_switch_in_s != null
                        ? `Next switch in ${Math.ceil(diagnostics.next_switch_in_s)}s`
                        : diagnostics.waiting_for_clock
                          ? "Waiting for confident beat alignment"
                          : "No switch queued"}
                    </span>
                  </div>
                  {diagnostics.alignment_estimated && (
                    <p className="text-[11px] leading-relaxed text-muted-foreground">
                      Bar and phrase positions use an estimated 4/4 grid anchored to the first observed beat.
                    </p>
                  )}
                </section>
              )
            }) : (
              <p className="text-xs text-muted-foreground">No output diagnostics are available.</p>
            )}
          </div>
        </details>
      </CardContent>
    </Card>
  )
}
