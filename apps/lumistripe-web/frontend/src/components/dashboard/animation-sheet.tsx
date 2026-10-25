import { useMemo, useState } from "react"
import { ArrowLeftIcon, CheckIcon, SearchIcon, Settings2Icon, SparklesIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
} from "@/lib/api"

interface AnimationSheetProps {
  animations: AnimationOption[]
  current: string
  disabled: boolean
  stripeId?: string
  settingsDisabledReason?: string
  onSelect: (name: string) => Promise<boolean>
  onLoadSettings: (name: string, stripeId?: string) => Promise<AnimationSettingsResponse>
  onSaveSettings: (
    name: string,
    values: Record<string, string | number | boolean>,
    stripeId?: string,
  ) => Promise<AnimationSettingsResponse>
  onResetSettings: (name: string, stripeId?: string) => Promise<AnimationSettingsResponse>
}

type SettingValue = string | number | boolean

function displayName(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ")
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
  if (option.type === "number") {
    const numberValue = typeof value === "number" ? value : option.minimum ?? 0
    const minimum = option.minimum ?? 0
    const maximum = option.maximum ?? 100
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <label className="text-sm font-medium" htmlFor={`animation-setting-${option.id}`}>
            {option.label}
          </label>
          <span className="text-sm tabular-nums text-muted-foreground">
            {numberValue}{option.unit ?? ""}
          </span>
        </div>
        <Slider
          value={[numberValue]}
          min={minimum}
          max={maximum}
          step={option.step ?? 1}
          disabled={disabled}
          aria-label={option.label}
          onValueChange={(next) => {
            const selected = Array.isArray(next) ? next[0] : next
            if (typeof selected === "number") onChange(selected)
          }}
        />
        <p className="text-xs leading-relaxed text-muted-foreground">{option.description}</p>
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
          aria-label={option.label}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.value)}
          className="h-10 w-full rounded-lg border border-white/10 bg-black/20 px-3 text-sm capitalize text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50"
        >
          {option.choices.map((choice) => (
            <option key={choice} value={choice} className="bg-background capitalize">
              {displayName(choice)}
            </option>
          ))}
        </select>
        <p className="text-xs leading-relaxed text-muted-foreground">{option.description}</p>
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
          aria-label={option.label}
          value={typeof value === "string" ? value : "#7C3AED"}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.value.toUpperCase())}
          className="h-11 cursor-pointer p-1"
        />
        <p className="text-xs leading-relaxed text-muted-foreground">{option.description}</p>
      </div>
    )
  }

  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <p className="text-sm font-medium">{option.label}</p>
        <p className="text-xs leading-relaxed text-muted-foreground">{option.description}</p>
      </div>
      <Toggle
        pressed={value === true}
        disabled={disabled}
        aria-label={option.label}
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
  onSelect,
  onLoadSettings,
  onSaveSettings,
  onResetSettings,
}: AnimationSheetProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [editing, setEditing] = useState<AnimationOption | null>(null)
  const [settings, setSettings] = useState<AnimationSettingsResponse | null>(null)
  const [draft, setDraft] = useState<Record<string, SettingValue>>({})
  const [settingsLoading, setSettingsLoading] = useState(false)
  const [settingsBusy, setSettingsBusy] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const normalizedQuery = query.trim().toLowerCase()
  const filteredAnimations = useMemo(
    () =>
      animations.filter((animation) =>
        `${displayName(animation.name)} ${displayName(animation.mood)}`
          .toLowerCase()
          .includes(normalizedQuery)
      ),
    [animations, normalizedQuery]
  )
  const dirty = settings !== null && JSON.stringify(settings.values) !== JSON.stringify(draft)

  const select = async (name: string) => {
    if (await onSelect(name)) {
      setOpen(false)
      setQuery("")
    }
  }

  const configure = async (animation: AnimationOption) => {
    if (settingsDisabledReason || disabled) return
    setEditing(animation)
    setSettings(null)
    setDraft({})
    setSettingsError(null)
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
    try {
      const response = await onSaveSettings(editing.name, draft, stripeId)
      setSettings(response)
      setDraft(response.values)
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : "Settings could not be saved.")
    } finally {
      setSettingsBusy(false)
    }
  }

  const reset = async () => {
    if (!editing) return
    setSettingsBusy(true)
    setSettingsError(null)
    try {
      const response = await onResetSettings(editing.name, stripeId)
      setSettings(response)
      setDraft(response.values)
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : "Settings could not be reset.")
    } finally {
      setSettingsBusy(false)
    }
  }

  const backToList = () => {
    setEditing(null)
    setSettings(null)
    setDraft({})
    setSettingsError(null)
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) backToList()
      }}
    >
      <SheetTrigger
        render={
          <Button
            variant="outline"
            aria-label={`Animation ${displayName(current)}. Change`}
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
            <span className="block truncate capitalize">{displayName(current) || "Choose an animation"}</span>
          </span>
        </span>
        <span className="text-muted-foreground">Change</span>
      </SheetTrigger>

      <SheetContent className="mx-auto w-full max-w-lg gap-0 overflow-hidden">
        <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-white/20" aria-hidden="true" />
        <SheetHeader>
          {editing ? (
            <div className="flex items-start gap-2">
              <Button variant="ghost" size="icon" aria-label="Back to animations" onClick={backToList}>
                <ArrowLeftIcon aria-hidden="true" />
              </Button>
              <div className="min-w-0">
                <SheetTitle className="capitalize">{displayName(editing.name)}</SheetTitle>
                <SheetDescription>Adjust the appearance and behavior for this animation.</SheetDescription>
              </div>
            </div>
          ) : (
            <>
              <SheetTitle>Choose animation</SheetTitle>
              <SheetDescription>Selecting an animation switches playback to Static mode.</SheetDescription>
            </>
          )}
        </SheetHeader>

        {editing ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
            {settingsLoading ? (
              <p role="status" className="py-10 text-center text-sm text-muted-foreground">Loading settings…</p>
            ) : settings ? (
              <>
                <div className="space-y-5">
                  {settings.options
                    .filter((option) => !option.visible_when_option || draft[option.visible_when_option] === option.visible_when_value)
                    .map((option) => (
                      <SettingControl
                        key={option.id}
                        option={option}
                        value={draft[option.id] ?? option.default}
                        disabled={settingsBusy || disabled}
                        onChange={(value) => setDraft((previous) => ({ ...previous, [option.id]: value }))}
                      />
                    ))}
                </div>
                {settingsError && <p className="mt-4 text-sm text-red-200" role="alert">{settingsError}</p>}
                <div className="mt-6 flex gap-2">
                  <Button variant="outline" className="flex-1" disabled={settingsBusy} onClick={() => void reset()}>
                    Reset to defaults
                  </Button>
                  <Button className="flex-1" disabled={settingsBusy || !dirty} onClick={() => void save()}>
                    {settingsBusy ? "Saving…" : "Save settings"}
                  </Button>
                </div>
              </>
            ) : (
              settingsError && <p className="py-8 text-sm text-red-200" role="alert">{settingsError}</p>
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
                placeholder="Search name or mood"
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
                <div className="space-y-1">
                  {filteredAnimations.map((animation) => {
                    const selected = animation.name === current
                    const configurable = animation.settings.length > 0
                    return (
                      <div key={animation.name} className="flex items-center gap-1 rounded-xl pr-1 transition-colors hover:bg-white/5">
                        <button
                          type="button"
                          className="flex min-h-14 min-w-0 flex-1 cursor-pointer items-center gap-3 rounded-xl px-3 py-2 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                          disabled={disabled}
                          onClick={() => void select(animation.name)}
                        >
                          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-white/5 text-muted-foreground">
                            {selected ? <CheckIcon className="text-violet-300" aria-hidden="true" /> : <SparklesIcon aria-hidden="true" />}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-medium capitalize">{displayName(animation.name)}</span>
                            <span className="block truncate text-xs capitalize text-muted-foreground">{displayName(animation.mood)}</span>
                          </span>
                          {animation.dynamic_safe && <Badge variant="secondary">Music-ready</Badge>}
                        </button>
                        {configurable && (
                          <button
                            type="button"
                            className="grid size-10 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-40"
                            aria-label={`Configure ${displayName(animation.name)}`}
                            title={settingsDisabledReason ?? `Configure ${displayName(animation.name)}`}
                            disabled={disabled || Boolean(settingsDisabledReason)}
                            onClick={() => void configure(animation)}
                          >
                            <Settings2Icon aria-hidden="true" />
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
