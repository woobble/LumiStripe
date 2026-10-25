import { lazy, Suspense, useMemo, useRef, useState } from "react"
import { ArrowLeftIcon, CheckIcon, EyeOffIcon, SearchIcon, Settings2Icon, SparklesIcon } from "lucide-react"

import { applyAnimationPreset, type AnimationPreset } from "@/components/dashboard/animation-presets"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { Input } from "@/components/ui/input"
import { Slider } from "@/components/ui/slider"
import { Toggle } from "@/components/ui/toggle"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import type {
  AnimationOption,
  AnimationSettingsResponse,
  AnimationSettingOption,
  StripeTopology,
} from "@/lib/api"
import type { PreviewConnectionStatus } from "@/hooks/use-preview-stream"
import type { PreviewFrame } from "@/lib/preview"

const PreviewCanvas = lazy(async () => {
  const module = await import("@/components/dashboard/preview-canvas")
  return { default: module.PreviewCanvas }
})

interface AnimationSheetProps {
  animations: AnimationOption[]
  current: string
  disabled: boolean
  stripeId?: string
  settingsDisabledReason?: string
  previewEnabled: boolean
  previewFrame: PreviewFrame | null
  previewConnection: PreviewConnectionStatus
  previewTopology: StripeTopology
  onEnablePreview: () => void
  onDisablePreview: () => void
  onEditorVisibilityChange: (visible: boolean) => void
  onSelect: (name: string) => Promise<boolean>
  onLoadSettings: (name: string, stripeId?: string) => Promise<AnimationSettingsResponse>
  onSaveSettings: (
    name: string,
    values: Record<string, string | number | boolean>,
    stripeId?: string,
  ) => Promise<AnimationSettingsResponse>
}

type SettingValue = string | number | boolean
type NavigationAction = "close" | "back"

const settingGroups = [
  {
    id: "appearance",
    label: "Appearance",
    options: new Set([
      "intensity", "minimum_intensity", "maximum_intensity", "density", "particle_size",
      "fade_time", "decay", "cooling", "sparking", "flash_rate", "duty_cycle",
      "variation", "pattern", "pulse_curve",
    ]),
  },
  {
    id: "movement",
    label: "Movement",
    options: new Set([
      "speed", "direction", "tail_length", "width", "spread", "frequency",
      "separation", "count", "chase_count",
    ]),
  },
  {
    id: "colors",
    label: "Colors",
    options: new Set(["palette", "custom_color", "color_spread"]),
  },
  {
    id: "music",
    label: "Music",
    options: new Set(["music_reactivity", "sensitivity", "beat_sensitivity"]),
  },
] as const

function displayName(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ")
}

function optionGroup(option: AnimationSettingOption) {
  return settingGroups.find((group) => group.options.has(option.id))?.id ?? "other"
}

function settingsDiffer(
  saved: Record<string, SettingValue>,
  draft: Record<string, SettingValue>,
) {
  const keys = new Set([...Object.keys(saved), ...Object.keys(draft)])
  return [...keys].some((key) => saved[key] !== draft[key])
}

function SettingControl({
  option,
  value,
  disabled,
  onChange,
}: {
  option: AnimationSettingOption
  value: SettingValue
  disabled: boolean
  onChange: (value: SettingValue) => void
}) {
  const descriptionId = `animation-setting-description-${option.id}`

  if (option.type === "number") {
    const numberValue = typeof value === "number" ? value : option.minimum ?? 0
    const minimum = option.minimum ?? 0
    const maximum = option.maximum ?? 100
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <span className="text-sm font-medium" id={`animation-setting-label-${option.id}`}>
            {option.label}
          </span>
          <output className="text-sm tabular-nums text-muted-foreground" aria-live="polite">
            {numberValue}{option.unit ?? ""}
          </output>
        </div>
        <Slider
          value={[numberValue]}
          min={minimum}
          max={maximum}
          step={option.step ?? 1}
          disabled={disabled}
          aria-label={option.label}
          aria-describedby={descriptionId}
          onValueChange={(next) => {
            const selected = Array.isArray(next) ? next[0] : next
            if (typeof selected === "number") onChange(selected)
          }}
        />
        <p id={descriptionId} className="text-xs leading-relaxed text-muted-foreground">
          {option.description}
        </p>
      </div>
    )
  }

  if (option.type === "enum") {
    return (
      <div className="space-y-2">
        <label className="block text-sm font-medium" htmlFor={`animation-setting-${option.id}`}>
          {option.label}
        </label>
        <select
          id={`animation-setting-${option.id}`}
          aria-describedby={descriptionId}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.value)}
          className="h-11 w-full rounded-lg border border-white/10 bg-black/20 px-3 text-sm capitalize text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50"
        >
          {option.choices.map((choice) => (
            <option key={choice} value={choice} className="bg-background capitalize">
              {displayName(choice)}
            </option>
          ))}
        </select>
        <p id={descriptionId} className="text-xs leading-relaxed text-muted-foreground">
          {option.description}
        </p>
      </div>
    )
  }

  if (option.type === "color") {
    return (
      <div className="space-y-2">
        <label className="block text-sm font-medium" htmlFor={`animation-setting-${option.id}`}>
          {option.label}
        </label>
        <Input
          id={`animation-setting-${option.id}`}
          type="color"
          aria-describedby={descriptionId}
          value={typeof value === "string" ? value : "#7C3AED"}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.value.toUpperCase())}
          className="h-11 cursor-pointer p-1"
        />
        <p id={descriptionId} className="text-xs leading-relaxed text-muted-foreground">
          {option.description}
        </p>
      </div>
    )
  }

  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <p className="text-sm font-medium" id={`animation-setting-label-${option.id}`}>
          {option.label}
        </p>
        <p id={descriptionId} className="text-xs leading-relaxed text-muted-foreground">
          {option.description}
        </p>
      </div>
      <Toggle
        pressed={value === true}
        disabled={disabled}
        aria-label={option.label}
        aria-describedby={descriptionId}
        onPressedChange={onChange}
      >
        {value === true ? "On" : "Off"}
      </Toggle>
    </div>
  )
}

export function AnimationSheet({
  animations,
  current,
  disabled,
  stripeId,
  settingsDisabledReason,
  previewEnabled,
  previewFrame,
  previewConnection,
  previewTopology,
  onEnablePreview,
  onDisablePreview,
  onEditorVisibilityChange,
  onSelect,
  onLoadSettings,
  onSaveSettings,
}: AnimationSheetProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [editing, setEditing] = useState<AnimationOption | null>(null)
  const [settings, setSettings] = useState<AnimationSettingsResponse | null>(null)
  const [draft, setDraft] = useState<Record<string, SettingValue>>({})
  const [settingsLoading, setSettingsLoading] = useState(false)
  const [settingsBusy, setSettingsBusy] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [settingsNotice, setSettingsNotice] = useState<string | null>(null)
  const [discarding, setDiscarding] = useState<NavigationAction | null>(null)
  const allowCloseRef = useRef(false)
  const normalizedQuery = query.trim().toLowerCase()
  const filteredAnimations = useMemo(
    () =>
      animations.filter((animation) =>
        `${animation.display_name} ${animation.name} ${displayName(animation.mood)} ${animation.description}`
          .toLowerCase()
          .includes(normalizedQuery),
      ),
    [animations, normalizedQuery],
  )
  const savedValues = settings?.values ?? {}
  const dirty = settings !== null && settingsDiffer(savedValues, draft)
  const baseAnimations = filteredAnimations.filter((animation) => animation.role === "base")
  const effectAnimations = filteredAnimations.filter((animation) => animation.role === "effect")

  const clearEditor = () => {
    onEditorVisibilityChange(false)
    setEditing(null)
    setSettings(null)
    setDraft({})
    setSettingsError(null)
    setSettingsNotice(null)
  }

  const completeNavigation = (action: NavigationAction) => {
    setDiscarding(null)
    clearEditor()
    if (action === "close") {
      allowCloseRef.current = true
      setOpen(false)
      setQuery("")
    }
  }

  const requestNavigation = (action: NavigationAction) => {
    if (dirty) {
      setDiscarding(action)
      return
    }
    completeNavigation(action)
  }

  const select = async (name: string) => {
    if (await onSelect(name)) {
      setOpen(false)
      setQuery("")
    }
  }

  const configure = async (animation: AnimationOption) => {
    if (settingsDisabledReason || disabled) return
    onEditorVisibilityChange(true)
    setEditing(animation)
    setSettings(null)
    setDraft({})
    setSettingsError(null)
    setSettingsNotice(null)
    setSettingsLoading(true)
    try {
      const response = await onLoadSettings(animation.name, stripeId)
      setSettings(response)
      setDraft(response.values)
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : "Settings could not be loaded.")
    } finally {
      setSettingsLoading(false)
    }
  }

  const save = async () => {
    if (!editing || !settings || !dirty) return
    setSettingsBusy(true)
    setSettingsError(null)
    setSettingsNotice(null)
    try {
      const response = await onSaveSettings(editing.name, draft, stripeId)
      setSettings(response)
      setDraft(response.values)
      setSettingsNotice(previewEnabled
        ? "Settings saved. The live preview now shows the applied output."
        : "Settings saved.")
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : "Settings could not be saved.")
    } finally {
      setSettingsBusy(false)
    }
  }

  const resetDraft = () => {
    if (!settings) return
    setDraft(Object.fromEntries(settings.options.map((option) => [option.id, option.default])))
    setSettingsError(null)
    setSettingsNotice("Defaults loaded into this draft. Save to apply them.")
  }

  const applyPreset = (preset: AnimationPreset) => {
    if (!settings) return
    setDraft((previous) => applyAnimationPreset(settings.options, previous, preset))
    setSettingsError(null)
    setSettingsNotice(`${preset[0]?.toUpperCase()}${preset.slice(1)} preset loaded into this draft.`)
  }

  const renderAnimationGroup = (groupId: string, label: string, items: AnimationOption[]) => {
    if (items.length === 0) return null
    return (
      <section key={groupId} className="space-y-1" aria-labelledby={`animation-group-${groupId}`}>
        <h3 id={`animation-group-${groupId}`} className="px-3 pt-3 pb-1 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </h3>
        {items.map((animation) => {
          const selected = animation.name === current
          const configurable = animation.settings.length > 0
          return (
            <div
              key={animation.name}
              className={`flex items-center gap-1 rounded-xl pr-1 transition-colors ${selected ? "bg-violet-400/10 ring-1 ring-inset ring-violet-300/20" : "hover:bg-white/5"}`}
            >
              <button
                type="button"
                className="flex min-h-20 min-w-0 flex-1 cursor-pointer items-start gap-3 rounded-xl px-3 py-3 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                disabled={disabled}
                aria-pressed={selected}
                onClick={() => void select(animation.name)}
              >
                <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg bg-white/5 text-muted-foreground">
                  {selected ? <CheckIcon className="text-violet-300" aria-hidden="true" /> : <SparklesIcon aria-hidden="true" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{animation.display_name}</span>
                    {selected && <Badge variant="default">Selected</Badge>}
                  </span>
                  <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                    {animation.description}
                  </span>
                  <span className="mt-2 flex flex-wrap gap-1.5">
                    <Badge variant="outline" className="capitalize">{displayName(animation.mood)}</Badge>
                    {animation.dynamic_safe && <Badge variant="secondary">Music-ready</Badge>}
                    {animation.sequence_eligible && <Badge variant="outline">Sequencer-ready</Badge>}
                  </span>
                </span>
              </button>
              {configurable && (
                <button
                  type="button"
                  className="grid size-11 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-40"
                  aria-label={`Configure ${animation.display_name}`}
                  title={settingsDisabledReason ?? `Configure ${animation.display_name}`}
                  disabled={disabled || Boolean(settingsDisabledReason)}
                  onClick={() => void configure(animation)}
                >
                  <Settings2Icon aria-hidden="true" />
                </button>
              )}
            </div>
          )
        })}
      </section>
    )
  }

  return (
    <>
      <Sheet
        open={open}
        onOpenChange={(next) => {
          if (next) {
            setOpen(true)
            return
          }
          if (dirty && !allowCloseRef.current) {
            setDiscarding("close")
            return
          }
          allowCloseRef.current = false
          setOpen(false)
          clearEditor()
        }}
      >
        <SheetTrigger
          render={
            <Button
              variant="outline"
              aria-label={`Animation ${animations.find((item) => item.name === current)?.display_name ?? displayName(current)}. Change`}
              className="h-auto min-h-14 w-full justify-between rounded-xl border-white/10 bg-white/[0.03] px-4 py-3 text-left"
              disabled={disabled}
            />
          }
        >
          <span className="flex min-w-0 items-center gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-violet-400/10 text-violet-300">
              <SparklesIcon aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block text-xs font-normal text-muted-foreground">Animation</span>
              <span className="block truncate">{animations.find((item) => item.name === current)?.display_name ?? displayName(current) ?? "Choose an animation"}</span>
            </span>
          </span>
          <span className="text-muted-foreground">Change</span>
        </SheetTrigger>

        <SheetContent className="mx-auto w-full max-w-2xl gap-0 overflow-hidden">
          <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-white/20" aria-hidden="true" />
          <SheetHeader>
            {editing ? (
              <div className="flex items-start gap-2">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Back to animations"
                  onClick={() => requestNavigation("back")}
                >
                  <ArrowLeftIcon aria-hidden="true" />
                </Button>
                <div className="min-w-0">
                  <SheetTitle>{editing.display_name}</SheetTitle>
                  <SheetDescription>{editing.description}</SheetDescription>
                </div>
              </div>
            ) : (
              <>
                <SheetTitle>Choose animation</SheetTitle>
                <SheetDescription>Choose a base animation or an audio-triggered effect. Selecting one switches playback to Static mode.</SheetDescription>
              </>
            )}
          </SheetHeader>

          {editing ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 pb-5">
                {settingsLoading ? (
                  <p role="status" className="py-10 text-center text-sm text-muted-foreground">Loading settings…</p>
                ) : settings ? (
                  <>
                    <section className="space-y-3 rounded-xl border border-white/10 bg-black/20 p-3" aria-label="Live animation preview">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <h3 className="text-sm font-medium">Live output</h3>
                        <div className="flex items-center gap-2">
                          <Badge variant={previewEnabled && previewConnection === "connected" && previewFrame ? "default" : "secondary"}>
                            {!previewEnabled ? "Off" : previewConnection === "connected" && previewFrame ? "Live" : "Connecting"}
                          </Badge>
                          <Badge variant="outline">Saved output</Badge>
                          {previewEnabled && (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label="Hide live preview"
                              title="Hide live preview"
                              onClick={onDisablePreview}
                            >
                              <EyeOffIcon aria-hidden="true" />
                            </Button>
                          )}
                        </div>
                      </div>
                      {previewEnabled ? (
                        <div className="overflow-hidden rounded-lg border border-white/10 bg-black/40">
                          <Suspense fallback={<div className="aspect-[2.2/1] animate-pulse bg-white/5" aria-label="Loading preview renderer" />}>
                            <PreviewCanvas
                              frame={previewFrame}
                              topology={previewTopology}
                              className="block aspect-[2.2/1] h-auto w-full"
                            />
                          </Suspense>
                        </div>
                      ) : (
                        <Button variant="outline" className="w-full" onClick={onEnablePreview}>
                          Enable live preview
                        </Button>
                      )}
                      <p className="text-xs leading-relaxed text-muted-foreground" role="status">
                        {dirty
                          ? "Unsaved edits are not applied to the LEDs. Save settings to update this preview."
                          : "This preview shows the settings currently saved on the selected output."}
                      </p>
                    </section>

                    <section aria-labelledby="animation-presets-heading" className="space-y-2">
                      <div>
                        <h3 id="animation-presets-heading" className="text-sm font-medium">Starting point</h3>
                        <p className="text-xs text-muted-foreground">Presets change the draft only. Your palette and movement choices stay as selected.</p>
                      </div>
                      <div className="grid grid-cols-3 gap-2" role="group" aria-label="Animation presets">
                        {(["subtle", "balanced", "energetic"] as const).map((preset) => (
                          <Button
                            key={preset}
                            variant="outline"
                            className="h-10 capitalize"
                            disabled={settingsBusy || disabled}
                            onClick={() => applyPreset(preset)}
                          >
                            {preset}
                          </Button>
                        ))}
                      </div>
                    </section>

                    {settingGroups.map((group) => {
                      const options = settings.options.filter(
                        (option) => optionGroup(option) === group.id
                          && (!option.visible_when_option || draft[option.visible_when_option] === option.visible_when_value),
                      )
                      if (options.length === 0) return null
                      return (
                        <section key={group.id} aria-labelledby={`setting-group-${group.id}`} className="space-y-4 rounded-xl border border-white/5 bg-white/[0.02] p-4">
                          <h3 id={`setting-group-${group.id}`} className="text-sm font-semibold">{group.label}</h3>
                          {options.map((option) => (
                            <SettingControl
                              key={option.id}
                              option={option}
                              value={draft[option.id] ?? option.default}
                              disabled={settingsBusy || disabled}
                              onChange={(value) => {
                                setDraft((previous) => ({ ...previous, [option.id]: value }))
                                setSettingsError(null)
                                setSettingsNotice(null)
                              }}
                            />
                          ))}
                        </section>
                      )
                    })}

                    {(() => {
                      const options = settings.options.filter(
                        (option) => optionGroup(option) === "other"
                          && (!option.visible_when_option || draft[option.visible_when_option] === option.visible_when_value),
                      )
                      return options.length > 0 ? (
                        <section aria-labelledby="setting-group-other" className="space-y-4 rounded-xl border border-white/5 bg-white/[0.02] p-4">
                          <h3 id="setting-group-other" className="text-sm font-semibold">Other</h3>
                          {options.map((option) => (
                            <SettingControl
                              key={option.id}
                              option={option}
                              value={draft[option.id] ?? option.default}
                              disabled={settingsBusy || disabled}
                              onChange={(value) => setDraft((previous) => ({ ...previous, [option.id]: value }))}
                            />
                          ))}
                        </section>
                      ) : null
                    })()}
                  </>
                ) : settingsError ? (
                  <div className="space-y-3 py-8 text-center">
                    <p className="text-sm text-red-200" role="alert">{settingsError}</p>
                    <Button variant="outline" onClick={() => void configure(editing)}>Try again</Button>
                  </div>
                ) : null}
              </div>

              {settings && (
                <div className="shrink-0 border-t border-white/10 bg-popover/95 px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))] backdrop-blur">
                  <div className="mb-2 flex min-h-5 items-center justify-between gap-3 text-xs">
                    <span className={dirty ? "text-amber-200" : "text-muted-foreground"} role="status">
                      {dirty ? "Unsaved changes" : settingsNotice ?? "All changes saved"}
                    </span>
                    {settingsError && <span className="text-right text-red-200" role="alert">{settingsError}</span>}
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      className="min-h-11 flex-1"
                      disabled={settingsBusy || disabled}
                      onClick={resetDraft}
                    >
                      Reset draft
                    </Button>
                    <Button
                      className="min-h-11 flex-1"
                      disabled={settingsBusy || disabled || !dirty}
                      onClick={() => void save()}
                    >
                      {settingsBusy ? "Saving…" : "Save settings"}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <>
              <div className="relative px-4 pb-3">
                <SearchIcon className="pointer-events-none absolute top-3 left-7 size-4 text-muted-foreground" aria-hidden="true" />
                <Input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search name, mood, or description"
                  aria-label="Search animations"
                  className="pl-10"
                />
              </div>
              {settingsDisabledReason && (
                <p className="px-4 pb-3 text-xs text-muted-foreground" role="status">{settingsDisabledReason}</p>
              )}
              <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-[max(1rem,env(safe-area-inset-bottom))]">
                {filteredAnimations.length === 0 ? (
                  <p className="px-4 py-12 text-center text-sm text-muted-foreground">No animations match “{query}”.</p>
                ) : (
                  <div className="space-y-3">
                    {renderAnimationGroup("base", "Base animations", baseAnimations)}
                    {renderAnimationGroup("effects", "Effects", effectAnimations)}
                  </div>
                )}
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>

      <AlertDialog
        open={discarding !== null}
        onOpenChange={(next) => {
          if (!next) setDiscarding(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved settings?</AlertDialogTitle>
            <AlertDialogDescription>Your changes have not been applied to the LEDs. Discard them and continue?</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setDiscarding(null)}>Keep editing</AlertDialogCancel>
            <AlertDialogAction onClick={() => discarding && completeNavigation(discarding)}>
              Discard changes
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
