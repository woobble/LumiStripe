import { useMemo, useState } from "react"
import { toast } from "sonner"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CopyIcon,
  ListPlusIcon,
  PlayIcon,
  PlusIcon,
  SaveIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react"

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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Switch } from "@/components/ui/switch"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"
import type {
  AnimationOption,
  AnimationSequenceContract,
  AnimationSequenceStepContract,
  DashboardState,
  SequenceCreateRequest,
  SequenceDefinitionRequest,
} from "@/lib/api"

type SequenceDraft = {
  id: string
  name: string
  repeat: boolean
  steps: AnimationSequenceStepContract[]
  creating: boolean
}

type SequenceDefinition = Omit<SequenceCreateRequest, "id">

type SequenceLibraryProps = {
  state: DashboardState
  target: string
  animations: AnimationOption[]
  animationLoading?: boolean
  animationLoadError?: string | null
  sequences: AnimationSequenceContract[]
  loading: boolean
  loadError: string | null
  actionError: string | null
  busy?: boolean
  saving: boolean
  onRefresh: () => Promise<void>
  onCreate: (sequence: SequenceCreateRequest) => Promise<boolean>
  onUpdate: (sequenceId: string, sequence: SequenceDefinitionRequest) => Promise<boolean>
  onDelete: (sequenceId: string) => Promise<boolean>
  onSetActive: (sequenceId: string | null) => Promise<boolean>
}

function displayName(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ")
}

function uniqueSequenceId(sequences: AnimationSequenceContract[]) {
  const existing = new Set(sequences.map(({ id }) => id))
  let id = ""
  do {
    id = typeof crypto !== "undefined" && "randomUUID" in crypto
      ? `sequence-${crypto.randomUUID()}`
      : `sequence-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
  } while (existing.has(id))
  return id.slice(0, 64)
}

function uniqueName(proposed: string, sequences: AnimationSequenceContract[]) {
  const existing = new Set(sequences.map(({ name }) => name.toLocaleLowerCase()))
  let suffix = 1
  let name = proposed.slice(0, 80)
  while (existing.has(name.toLocaleLowerCase())) {
    suffix += 1
    const ending = ` (${suffix})`
    name = `${proposed.slice(0, 80 - ending.length)}${ending}`
  }
  return name
}

function cloneSteps(steps: AnimationSequenceStepContract[]) {
  return steps.map((step) => ({ ...step }))
}

function equalDefinition(draft: SequenceDraft, source: AnimationSequenceContract) {
  return draft.name === source.name
    && draft.repeat === source.repeat
    && JSON.stringify(draft.steps) === JSON.stringify(source.steps)
}

function formatDuration(seconds: number) {
  const rounded = Math.round(seconds)
  const minutes = Math.floor(rounded / 60)
  return `${minutes}:${String(rounded % 60).padStart(2, "0")}`
}

function outputRows(state: DashboardState, target: string) {
  if (state.stripe_topology.layout !== "independent") return state.stripe_playback.slice(0, 1)
  return target === "all"
    ? state.stripe_playback
    : state.stripe_playback.filter((output) => output.stripe_id === target)
}

function outputName(state: DashboardState, stripeId: string) {
  return state.stripe_topology.outputs.find((output) => output.id === stripeId)?.name ?? stripeId
}

function createDefaultDraft(
  animations: AnimationOption[],
  sequences: AnimationSequenceContract[],
): SequenceDraft | null {
  const firstAnimation = animations.find((animation) => animation.sequence_eligible)
  if (!firstAnimation) return null
  return {
    id: uniqueSequenceId(sequences),
    name: uniqueName("New sequence", sequences),
    repeat: true,
    steps: [{ animation_name: firstAnimation.name, duration_bars: 1 }],
    creating: true,
  }
}

export function SequenceLibrary({
  state,
  target,
  animations,
  animationLoading = false,
  animationLoadError = null,
  sequences,
  loading,
  loadError,
  actionError,
  busy = false,
  saving,
  onRefresh,
  onCreate,
  onUpdate,
  onDelete,
  onSetActive,
}: SequenceLibraryProps) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<SequenceDraft | null>(null)
  const [sourceSequence, setSourceSequence] = useState<AnimationSequenceContract | null>(null)
  const [discardPrompt, setDiscardPrompt] = useState(false)
  const [discardDestination, setDiscardDestination] = useState<"close" | "library">("close")
  const [deleteTarget, setDeleteTarget] = useState<AnimationSequenceContract | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)

  const activeSequenceId = state.sequence_library?.active_sequence_id ?? null
  const blocked = busy || saving
  const eligibleAnimations = useMemo(
    () => animations.filter((animation) => animation.sequence_eligible),
    [animations],
  )
  const rows = outputRows(state, target)
  const isDirty = draft !== null && (
    draft.creating
      || sourceSequence === null
      || !equalDefinition(draft, sourceSequence)
  )
  useUnsavedChangesGuard(open && isDirty, "Your unsaved sequence edits will be discarded if you leave this page.")

  const requestClose = (nextOpen: boolean) => {
    if (!nextOpen && isDirty) {
      setDiscardDestination("close")
      setDiscardPrompt(true)
      return
    }
    setOpen(nextOpen)
    if (!nextOpen) {
      setDraft(null)
      setSourceSequence(null)
      setValidationError(null)
    }
  }

  const requestEditorBack = () => {
    if (isDirty) {
      setDiscardDestination("library")
      setDiscardPrompt(true)
      return
    }
    setDraft(null)
    setSourceSequence(null)
  }

  const startCreate = () => {
    const nextDraft = createDefaultDraft(eligibleAnimations, sequences)
    if (!nextDraft) {
      toast.error("There are no Dynamic-eligible animations available for a sequence.")
      return
    }
    setDraft(nextDraft)
    setSourceSequence(null)
    setValidationError(null)
  }

  const startEdit = (sequence: AnimationSequenceContract) => {
    setDraft({ ...sequence, steps: cloneSteps(sequence.steps), creating: false })
    setSourceSequence(sequence)
    setValidationError(null)
  }

  const startDuplicate = (sequence: AnimationSequenceContract) => {
    if (sequences.length >= 20) return
    setDraft({
      id: uniqueSequenceId(sequences),
      name: uniqueName(`Copy of ${sequence.name}`, sequences),
      repeat: sequence.repeat,
      steps: cloneSteps(sequence.steps),
      creating: true,
    })
    setSourceSequence(null)
    setValidationError(null)
  }

  const changeStep = (index: number, values: Partial<AnimationSequenceStepContract>) => {
    setDraft((current) => current === null ? current : {
      ...current,
      steps: current.steps.map((step, stepIndex) => stepIndex === index ? { ...step, ...values } : step),
    })
  }

  const moveStep = (index: number, delta: -1 | 1) => {
    setDraft((current) => {
      if (!current) return current
      const nextIndex = index + delta
      if (nextIndex < 0 || nextIndex >= current.steps.length) return current
      const steps = cloneSteps(current.steps)
      const [step] = steps.splice(index, 1)
      if (!step) return current
      steps.splice(nextIndex, 0, step)
      return { ...current, steps }
    })
  }

  const validateDraft = (value: SequenceDraft): string | null => {
    const name = value.name.trim()
    if (!name) return "Enter a sequence name."
    if (name.length > 80) return "Sequence names can be at most 80 characters."
    if (value.steps.length < 1) return "Add at least one animation step."
    if (value.steps.length > 32) return "A sequence can contain at most 32 steps."
    if (value.steps.some((step) => !eligibleAnimations.some((animation) => animation.name === step.animation_name))) {
      return "Every step must use a Dynamic-eligible animation. Choose an eligible animation for each step."
    }
    return null
  }

  const saveDraft = async () => {
    if (!draft || blocked) return
    const problem = validateDraft(draft)
    if (problem) {
      setValidationError(problem)
      return
    }
    setValidationError(null)
    const definition: SequenceDefinition = {
      name: draft.name.trim(),
      repeat: draft.repeat,
      steps: cloneSteps(draft.steps),
    }
    const saved = draft.creating
      ? await onCreate({ id: draft.id, ...definition })
      : await onUpdate(draft.id, definition)
    if (saved) {
      setDraft(null)
      setSourceSequence(null)
    }
  }

  const duplicateStep = (index: number) => {
    setDraft((current) => {
      if (!current || current.steps.length >= 32) return current
      const step = current.steps[index]
      if (!step) return current
      const steps = cloneSteps(current.steps)
      steps.splice(index + 1, 0, { ...step })
      return { ...current, steps }
    })
  }

  const addStep = () => {
    const firstAnimation = eligibleAnimations[0]
    setDraft((current) => {
      if (!current || !firstAnimation || current.steps.length >= 32) return current
      return {
        ...current,
        steps: [...current.steps, { animation_name: firstAnimation.name, duration_bars: 1 }],
      }
    })
  }

  const draftTotalBars = draft?.steps.reduce((total, step) => total + step.duration_bars, 0) ?? 0
  const bpm = rows.find((output) => output.sequencer_diagnostics?.bpm && output.sequencer_diagnostics.bpm > 0)?.sequencer_diagnostics?.bpm ?? 0
  const estimatedSeconds = bpm > 0 ? draftTotalBars * 4 * 60 / bpm : null

  return (
    <>
      <Button variant="outline" className="w-full justify-between" onClick={() => setOpen(true)} aria-label="Open sequence library">
        <span className="flex items-center gap-2"><ListPlusIcon aria-hidden="true" />Custom animation sequences</span>
        <span className="text-xs text-muted-foreground">{sequences.length} / 20</span>
      </Button>

      <Sheet open={open} onOpenChange={requestClose}>
        <SheetContent
          side="bottom"
          showCloseButton={false}
          className="mx-auto h-[100svh] max-h-[100svh] data-[side=bottom]:max-h-[100svh] w-full max-w-5xl gap-0 overflow-hidden rounded-none border-x-0 border-b-0 p-0 sm:inset-y-1/2 sm:top-1/2 sm:bottom-auto sm:h-[min(90svh,56rem)] sm:max-h-[90svh] sm:-translate-y-1/2 sm:rounded-2xl sm:border"
        >
          <SheetHeader className="shrink-0 border-b border-white/5 px-4 py-4 pr-16 sm:px-6">
            <SheetTitle>Animation sequences</SheetTitle>
            <SheetDescription>Build repeatable animation sets on the estimated musical bar grid.</SheetDescription>
          </SheetHeader>
          <Button type="button" variant="ghost" size="icon" className="absolute top-3 right-3 z-10 size-11" onClick={() => requestClose(false)} aria-label="Close sequence library">
            <XIcon aria-hidden="true" />
          </Button>

          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:px-6">
            {draft ? (
              <section className="mx-auto max-w-3xl space-y-5" aria-label="Sequence editor">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-lg font-semibold">{draft.creating ? "Create sequence" : "Edit sequence"}</h3>
                    <p className="mt-1 text-sm text-muted-foreground">Each step starts at a confident estimated bar boundary.</p>
                  </div>
                  <Button variant="ghost" size="sm" onClick={requestEditorBack} disabled={blocked} aria-label="Back to sequence library">
                    <XIcon aria-hidden="true" />Library
                  </Button>
                </div>

                <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
                  <label className="space-y-2 text-sm font-medium" htmlFor="sequence-name">
                    Sequence name
                    <Input id="sequence-name" value={draft.name} maxLength={80} disabled={blocked} onChange={(event) => setDraft((current) => current ? { ...current, name: event.target.value } : current)} aria-invalid={Boolean(validationError && !draft.name.trim())} />
                    <span className="block text-xs font-normal text-muted-foreground">{draft.name.length} / 80 characters</span>
                  </label>
                  <label className="flex min-h-11 items-center justify-between gap-3 rounded-xl border border-white/10 px-3 text-sm" htmlFor="sequence-repeat">
                    <span>Repeat sequence</span>
                    <Switch id="sequence-repeat" checked={draft.repeat} disabled={blocked} onCheckedChange={(repeat) => setDraft((current) => current ? { ...current, repeat } : current)} aria-label="Repeat sequence" />
                  </label>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h4 className="font-medium">Steps <span className="text-xs text-muted-foreground">{draft.steps.length} / 32</span></h4>
                    <p className="text-xs text-muted-foreground">Use the arrow buttons to reorder steps by keyboard or touch.</p>
                  </div>
                  <Button type="button" variant="outline" size="sm" onClick={addStep} disabled={blocked || draft.steps.length >= 32 || eligibleAnimations.length === 0}>
                    <PlusIcon aria-hidden="true" />Add step
                  </Button>
                </div>

                {animationLoading && <p className="rounded-lg border border-white/10 p-3 text-sm text-muted-foreground" role="status">Loading sequence-eligible animations…</p>}
                {!animationLoading && animationLoadError && <p className="rounded-lg border border-red-400/20 bg-red-400/5 p-3 text-sm text-red-200" role="alert">Eligible animations could not be loaded: {animationLoadError}</p>}
                {!animationLoading && !animationLoadError && eligibleAnimations.length === 0 && (
                  <p className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-3 text-sm text-amber-100" role="alert">
                    No Dynamic-eligible animations are available for sequence steps.
                  </p>
                )}
                {animationLoadError && <Button type="button" variant="outline" onClick={() => void onRefresh()}>Retry animation list</Button>}

                <div className="space-y-3" aria-label="Ordered sequence steps">
                  {draft.steps.map((step, index) => {
                    const current = rows.find((output) => output.sequence_playback?.selected_sequence_id === draft.id)?.sequence_playback
                    const playingStep = current?.active && current.current_step_index === index
                    return (
                      <article key={`${draft.id}-${index}`} className={`rounded-xl border p-3 ${playingStep ? "border-fuchsia-300/60 bg-fuchsia-400/10" : "border-white/10 bg-black/15"}`} aria-label={`Step ${index + 1}${playingStep ? ", currently playing" : ""}`}>
                        <div className="mb-3 flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <span className="grid size-7 place-items-center rounded-lg bg-white/5 text-xs font-semibold">{index + 1}</span>
                            {playingStep && <Badge variant="secondary">Playing</Badge>}
                          </div>
                          <div className="flex items-center gap-1">
                            <Button type="button" variant="ghost" size="icon" className="size-10" disabled={blocked || index === 0} onClick={() => moveStep(index, -1)} aria-label={`Move step ${index + 1} up`}><ArrowUpIcon aria-hidden="true" /></Button>
                            <Button type="button" variant="ghost" size="icon" className="size-10" disabled={blocked || index === draft.steps.length - 1} onClick={() => moveStep(index, 1)} aria-label={`Move step ${index + 1} down`}><ArrowDownIcon aria-hidden="true" /></Button>
                            <Button type="button" variant="ghost" size="icon" className="size-10" disabled={blocked || draft.steps.length >= 32} onClick={() => duplicateStep(index)} aria-label={`Duplicate step ${index + 1}`}><CopyIcon aria-hidden="true" /></Button>
                            <Button type="button" variant="ghost" size="icon" className="size-10 text-red-200 hover:text-red-100" disabled={blocked || draft.steps.length <= 1} onClick={() => setDraft((value) => value ? { ...value, steps: value.steps.filter((_, stepIndex) => stepIndex !== index) } : value)} aria-label={`Remove step ${index + 1}`}><Trash2Icon aria-hidden="true" /></Button>
                          </div>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_9rem]">
                          <label className="space-y-1.5 text-xs font-medium" htmlFor={`sequence-animation-${index}`}>
                            Animation
                            <select
                              id={`sequence-animation-${index}`}
                              className="h-11 w-full rounded-xl border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                              value={step.animation_name}
                              disabled={blocked}
                              onChange={(event) => changeStep(index, { animation_name: event.target.value })}
                              aria-label={`Animation for step ${index + 1}`}
                            >
                              {eligibleAnimations.map((animation) => <option key={animation.name} value={animation.name}>{displayName(animation.name)}</option>)}
                            </select>
                          </label>
                          <label className="space-y-1.5 text-xs font-medium" htmlFor={`sequence-duration-${index}`}>
                            Duration
                            <select
                              id={`sequence-duration-${index}`}
                              className="h-11 w-full rounded-xl border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                              value={step.duration_bars}
                              disabled={blocked}
                              onChange={(event) => changeStep(index, { duration_bars: Number(event.target.value) as AnimationSequenceStepContract["duration_bars"] })}
                              aria-label={`Duration for step ${index + 1}`}
                            >
                              {[1, 2, 4, 8].map((bars) => <option key={bars} value={bars}>{bars} {bars === 1 ? "bar" : "bars"}</option>)}
                            </select>
                          </label>
                        </div>
                      </article>
                    )
                  })}
                </div>

                <div className="rounded-xl border border-white/10 bg-black/15 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span className="font-medium">Total: {draftTotalBars} {draftTotalBars === 1 ? "bar" : "bars"}</span>
                    <span className="text-xs text-muted-foreground">
                      {estimatedSeconds === null ? "Duration appears when BPM is available" : `About ${formatDuration(estimatedSeconds)} at ${Math.round(bpm)} BPM`}
                    </span>
                  </div>
                  <div className="mt-3 flex h-12 gap-1" aria-label="Sequence timeline">
                    {draft.steps.map((step, index) => {
                      const current = rows.find((output) => output.sequence_playback?.selected_sequence_id === draft.id)?.sequence_playback
                      const isCurrent = current?.active && current.current_step_index === index
                      const elapsed = isCurrent && current.remaining_bars !== null
                        ? Math.max(0, step.duration_bars - Math.min(step.duration_bars, current.remaining_bars ?? 0))
                        : 0
                      const progress = isCurrent ? elapsed / step.duration_bars * 100 : 0
                      return (
                        <div
                          key={`${index}-${step.animation_name}`}
                          className={`relative min-w-0 overflow-hidden rounded-lg border px-2 py-1.5 ${isCurrent ? "border-fuchsia-300 bg-fuchsia-400/20" : "border-white/10 bg-white/[0.03]"}`}
                          style={{ flexGrow: step.duration_bars, flexBasis: 0 }}
                          aria-current={isCurrent ? "step" : undefined}
                          aria-label={`Step ${index + 1}: ${displayName(step.animation_name)}, ${step.duration_bars} ${step.duration_bars === 1 ? "bar" : "bars"}${isCurrent ? ", current step" : ""}`}
                        >
                          <div className="absolute inset-y-0 left-0 bg-fuchsia-300/15" style={{ width: `${progress}%` }} aria-hidden="true" />
                          <span className="relative block truncate text-[11px] font-medium">{displayName(step.animation_name)}</span>
                          <span className="relative block text-[10px] text-muted-foreground">{step.duration_bars} bars</span>
                        </div>
                      )
                    })}
                  </div>
                  {rows.filter((output) => output.sequence_playback?.selected_sequence_id === draft.id).map((output) => {
                    const playback = output.sequence_playback
                    if (!playback) return null
                    const index = playback.current_step_index
                    const title = state.stripe_topology.layout === "independent" ? outputName(state, output.stripe_id) : "Output"
                    const status = playback.active && typeof index === "number"
                      ? `Step ${index + 1} · ${playback.remaining_bars ?? "—"} bars remaining${typeof playback.next_transition_in_s === "number" ? ` · next transition in ${Math.ceil(playback.next_transition_in_s)}s` : ""}`
                      : playback.waiting_for_clock ? "Waiting for clock" : "Not currently playing"
                    return <p key={output.stripe_id} className="mt-2 text-xs text-muted-foreground">{title}: {status}</p>
                  })}
                  <p className="mt-2 text-[11px] text-muted-foreground">Bar positions use an estimated grid, not confirmed musical downbeats.</p>
                </div>

                {(validationError || actionError) && <p role="alert" className="rounded-lg border border-red-400/20 bg-red-400/5 p-3 text-sm text-red-200">{validationError ?? actionError}</p>}

                <div className="sticky bottom-0 flex flex-col-reverse gap-2 border-t border-white/5 bg-background/95 py-3 backdrop-blur sm:flex-row sm:justify-end">
                  <Button type="button" variant="outline" className="min-h-11" disabled={blocked} onClick={() => requestClose(false)}>Cancel</Button>
                  <Button type="button" className="min-h-11" disabled={blocked || !isDirty || eligibleAnimations.length === 0} onClick={() => void saveDraft()}>
                    <SaveIcon aria-hidden="true" />{saving ? "Saving…" : draft.creating ? "Save sequence" : "Save changes"}
                  </Button>
                </div>
              </section>
            ) : (
              <div className="mx-auto max-w-4xl space-y-5">
                <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
                  <div>
                    <h3 className="text-lg font-semibold">Saved sequences</h3>
                    <p className="text-sm text-muted-foreground">Select one to use it in Dynamic mode, or leave it deactivated for automatic selection.</p>
                  </div>
                  <Button className="min-h-11" disabled={blocked || loading || sequences.length >= 20 || animationLoading || eligibleAnimations.length === 0} onClick={startCreate}>
                    <PlusIcon aria-hidden="true" />New sequence
                  </Button>
                </div>

                {loadError && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-400/20 bg-red-400/5 p-3 text-sm text-red-200"><span>{loadError}</span><Button type="button" size="sm" variant="outline" onClick={() => void onRefresh()}>Retry</Button></div>}
                {animationLoading && <p role="status" className="text-sm text-muted-foreground">Loading eligible animations…</p>}
                {animationLoadError && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-400/20 bg-red-400/5 p-3 text-sm text-red-200"><span>{animationLoadError}</span><Button type="button" size="sm" variant="outline" onClick={() => void onRefresh()}>Retry animation list</Button></div>}
                {!animationLoading && !animationLoadError && eligibleAnimations.length === 0 && <p className="text-sm text-amber-100">No Dynamic-eligible animations are available for sequences.</p>}
                {actionError && <p role="alert" className="rounded-lg border border-red-400/20 bg-red-400/5 p-3 text-sm text-red-200">{actionError}</p>}
                {loading ? (
                  <p className="rounded-xl border border-white/5 p-8 text-center text-sm text-muted-foreground" role="status">Loading saved sequences…</p>
                ) : sequences.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-white/10 p-8 text-center">
                    <p className="font-medium">No saved sequences yet</p>
                    <p className="mt-1 text-sm text-muted-foreground">Create a sequence to arrange animations across bars.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {sequences.map((sequence) => {
                      const selected = activeSequenceId === sequence.id
                      const progressRows = rows
                        .map((output) => ({ output, playback: output.sequence_playback }))
                        .filter(({ playback }) => playback?.selected_sequence_id === sequence.id)
                      const playing = progressRows.some(({ playback }) => playback?.active)
                      const waiting = progressRows.some(({ playback }) => playback?.waiting_for_clock)
                      const targetDynamic = rows.some((output) => output.mode === "dynamic")
                      const musicActive = rows.some((output) => output.music_active)
                      const starting = selected && targetDynamic && progressRows.some(({ playback }) =>
                        playback !== undefined
                        && (playback.current_step_index === null || playback.current_step_index === undefined)
                        && !playback.active
                        && !playback.waiting_for_clock
                        && typeof playback.next_transition_in_s === "number",
                      )
                      const finished = selected && progressRows.some(({ playback }) =>
                        playback !== undefined
                        && playback.current_step_index !== null
                        && playback.current_step_index !== undefined
                        && playback.remaining_bars === 0
                        && !playback.active
                        && !playback.waiting_for_clock
                        && playback.next_transition_in_s == null,
                      )
                      const totalBars = sequence.steps.reduce((total, step) => total + step.duration_bars, 0)
                      return (
                        <article key={sequence.id} className={`rounded-xl border p-3 sm:p-4 ${selected ? "border-violet-300/40 bg-violet-400/[0.06]" : "border-white/10 bg-black/10"}`}>
                          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <h4 className="truncate font-semibold">{sequence.name}</h4>
                                {selected && <Badge variant="secondary">Selected</Badge>}
                                {playing && <Badge>Playing</Badge>}
                                {waiting && <Badge variant="outline">Waiting for clock</Badge>}
                                {starting && <Badge variant="outline">Starting at next bar</Badge>}
                                {finished && <Badge variant="outline">Finished · automatic selection resumed</Badge>}
                              </div>
                              <p className="mt-1 text-xs text-muted-foreground">{sequence.steps.length} {sequence.steps.length === 1 ? "step" : "steps"} · {totalBars} bars · {sequence.repeat ? "Repeats" : "Does not repeat"}</p>
                              {rows.length > 0 && <p className="mt-2 text-xs text-muted-foreground">Current animation{rows.length > 1 ? "s" : ""}: {rows.map((output) => `${state.stripe_topology.layout === "independent" ? `${outputName(state, output.stripe_id)}: ` : ""}${displayName(output.animation)}`).join(" · ")}</p>}
                              <div className="mt-3 flex h-9 gap-1" aria-label={`${sequence.name} timeline`}>
                                {sequence.steps.map((step, index) => {
                                  const activeIndex = progressRows.find(({ playback }) => playback?.active)?.playback?.current_step_index
                                  const isCurrent = playing && activeIndex === index
                                  return <div key={`${sequence.id}-${index}`} className={`min-w-0 truncate rounded-md border px-2 py-1 text-[10px] ${isCurrent ? "border-fuchsia-300 bg-fuchsia-400/20 text-fuchsia-100" : "border-white/10 bg-white/[0.03] text-muted-foreground"}`} style={{ flexGrow: step.duration_bars, flexBasis: 0 }} aria-current={isCurrent ? "step" : undefined}>{displayName(step.animation_name)} · {step.duration_bars}b</div>
                                })}
                              </div>
                              {playing && progressRows.map(({ output, playback }) => playback?.active ? (
                                <p key={output.stripe_id} className="mt-2 text-xs text-muted-foreground">
                                  {state.stripe_topology.layout === "independent" ? `${outputName(state, output.stripe_id)}: ` : ""}Step {(playback.current_step_index ?? 0) + 1}: {displayName(playback.animation_name ?? "")} · {playback.remaining_bars ?? "—"} bars remaining
                                  {typeof playback.next_transition_in_s === "number" ? ` · next transition in ${Math.ceil(playback.next_transition_in_s)}s` : ""}
                                </p>
                              ) : null)}
                              {waiting && progressRows.map(({ output, playback }) => playback?.waiting_for_clock ? <p key={output.stripe_id} className="mt-2 text-xs text-amber-100">{state.stripe_topology.layout === "independent" ? `${outputName(state, output.stripe_id)}: ` : ""}Waiting for a confident beat clock before sequence playback starts.</p> : null)}
                              {starting && progressRows.map(({ output, playback }) => (playback?.current_step_index === null || playback?.current_step_index === undefined) && typeof playback?.next_transition_in_s === "number" ? <p key={output.stripe_id} className="mt-2 text-xs text-muted-foreground">{state.stripe_topology.layout === "independent" ? `${outputName(state, output.stripe_id)}: ` : ""}Starts on the estimated bar in {Math.ceil(playback.next_transition_in_s)}s.</p> : null)}
                              {selected && !targetDynamic && <p className="mt-2 text-xs text-muted-foreground">Dynamic mode is required to run this selected sequence.</p>}
                              {selected && targetDynamic && !musicActive && !playing && !waiting && <p className="mt-2 text-xs text-muted-foreground">Selected; waiting for Dynamic music activity.</p>}
                            </div>
                            <div className="grid grid-cols-2 gap-2 sm:w-40 sm:grid-cols-1">
                              <Button type="button" size="sm" className="min-h-10" disabled={blocked} onClick={() => void onSetActive(selected ? null : sequence.id)}>
                                <PlayIcon aria-hidden="true" />{selected ? "Deactivate" : "Activate"}
                              </Button>
                              <Button type="button" size="sm" variant="outline" className="min-h-10" disabled={blocked} onClick={() => startEdit(sequence)}>Edit</Button>
                              <Button type="button" size="sm" variant="outline" className="min-h-10" disabled={blocked || sequences.length >= 20} onClick={() => startDuplicate(sequence)}><CopyIcon aria-hidden="true" />Duplicate</Button>
                              <Button type="button" size="sm" variant="outline" className="min-h-10 text-red-200" disabled={blocked} onClick={() => setDeleteTarget(sequence)}><Trash2Icon aria-hidden="true" />Delete</Button>
                            </div>
                          </div>
                        </article>
                      )
                    })}
                  </div>
                )}
                {sequences.length >= 20 && <p className="text-xs text-muted-foreground">The library has reached its 20-sequence limit.</p>}
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <AlertDialog open={discardPrompt} onOpenChange={setDiscardPrompt}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved sequence edits?</AlertDialogTitle>
            <AlertDialogDescription>Your changes have not been saved. You can stay in the editor or discard them.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction onClick={() => {
              setDiscardPrompt(false)
              setDraft(null)
              setSourceSequence(null)
              setValidationError(null)
              if (discardDestination === "close") setOpen(false)
            }}>Discard edits</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={deleteTarget !== null} onOpenChange={(value) => { if (!value) setDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleteTarget?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>This permanently removes the saved sequence. If it is selected, playback will return to automatic Dynamic selection.</AlertDialogDescription>
          </AlertDialogHeader>
          {actionError && <p role="alert" className="text-sm text-red-200">{actionError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={blocked}>Cancel</AlertDialogCancel>
            <AlertDialogAction disabled={blocked} onClick={() => {
              if (!deleteTarget) return
              const id = deleteTarget.id
              void onDelete(id).then((deleted) => { if (deleted) setDeleteTarget(null) })
            }}>Delete sequence</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
