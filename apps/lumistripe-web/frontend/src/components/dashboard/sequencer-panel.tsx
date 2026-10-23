import { useState } from "react"
import { SaveIcon, SparklesIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type {
  DashboardState,
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

const presets: Array<{ label: string; value: SwitchQuantization }> = [
  { label: "Free", value: "off" },
  { label: "Every Beat", value: "beat" },
  { label: "Every Bar", value: "bar" },
  { label: "Every Phrase", value: "phrase" },
]

type SequencerPanelProps = {
  state: DashboardState
  target: string
  disabled: boolean
  saving: boolean
  onApply: (mode: SwitchQuantization, barsPerPhrase: 4 | 8 | 16) => Promise<boolean>
}

export function SequencerPanel({
  state,
  target,
  disabled,
  saving,
  onApply,
}: SequencerPanelProps) {
  const settings = state.sequencer_settings ?? defaultSettings
  const [modeDraft, setModeDraft] = useState<SwitchQuantization | null>(null)
  const [barsDraft, setBarsDraft] = useState<4 | 8 | 16 | null>(null)
  const mode = modeDraft ?? settings.switch_quantization
  const bars = barsDraft ?? settings.bars_per_phrase
  const dirty = mode !== settings.switch_quantization || bars !== settings.bars_per_phrase

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
    <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2">
          <SparklesIcon className="size-4 text-fuchsia-300" aria-hidden="true" />
          Sequencer
        </CardTitle>
        <CardDescription>Choose when Dynamic mode may change animations.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <div className="text-xs font-medium text-muted-foreground">Animation switch timing</div>
          <ToggleGroup
            value={[mode]}
            onValueChange={(values) => {
              const selected = values[0] as SwitchQuantization | undefined
              if (selected) setModeDraft(selected)
            }}
            disabled={disabled || saving}
            variant="outline"
            spacing={1}
            className="grid w-full grid-cols-2 rounded-xl bg-black/20 p-1 sm:grid-cols-4"
            aria-label="Animation switch timing"
          >
            {timingOptions.map(({ value, label }) => (
              <ToggleGroupItem
                key={value}
                value={value}
                className="h-10 min-w-0 rounded-lg border-0 px-2 text-xs data-pressed:bg-violet-400/15 data-pressed:text-violet-100"
              >
                {label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>

        <div className="space-y-2">
          <div className="text-xs font-medium text-muted-foreground">Bars per phrase</div>
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
            className="grid w-full grid-cols-3 rounded-xl bg-black/20 p-1"
            aria-label="Bars per phrase"
          >
            {([4, 8, 16] as const).map((value) => (
              <ToggleGroupItem
                key={value}
                value={String(value)}
                className="h-10 rounded-lg border-0 px-2 text-xs data-pressed:bg-violet-400/15 data-pressed:text-violet-100"
              >
                {value} bars
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Sequencer presets">
          {presets.map((preset) => (
            <Button
              key={preset.label}
              type="button"
              size="sm"
              variant={mode === preset.value ? "secondary" : "outline"}
              disabled={disabled || saving}
              aria-label={`${preset.label} preset`}
              onClick={() => setModeDraft(preset.value)}
            >
              {preset.label}
            </Button>
          ))}
        </div>

        <Button
          className="w-full"
          disabled={disabled || saving || !dirty}
          onClick={() => void apply()}
          aria-label="Apply and save sequencer settings"
        >
          <SaveIcon aria-hidden="true" />
          {saving ? "Saving…" : "Apply and save settings"}
        </Button>

        <div className="space-y-3 border-t border-white/5 pt-3" aria-label="Sequencer diagnostics">
          {rows.length > 0 ? rows.map((output) => {
            const diagnostics = output.sequencer_diagnostics ?? defaultDiagnostics
            const name = state.stripe_topology.layout === "independent"
              ? state.stripe_topology.outputs.find((stripe) => stripe.id === output.stripe_id)?.name ?? output.stripe_id
              : null
            return (
              <section key={output.stripe_id} className="space-y-2 rounded-lg bg-black/20 p-3" aria-label={name ? `${name} sequencer status` : "Sequencer status"}>
                {name && <div className="text-xs font-medium text-violet-200">{name}</div>}
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
      </CardContent>
    </Card>
  )
}
