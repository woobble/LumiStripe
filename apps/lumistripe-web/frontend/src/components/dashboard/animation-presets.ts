import type { AnimationSettingOption } from "@/lib/api"

export type AnimationPreset = "subtle" | "balanced" | "energetic"

const HIGHER_IS_MORE_ENERGETIC = new Set([
  "speed",
  "flash_rate",
  "frequency",
  "intensity",
  "minimum_intensity",
  "maximum_intensity",
  "density",
  "particle_size",
  "tail_length",
  "width",
  "spread",
  "separation",
  "count",
  "chase_count",
  "duty_cycle",
  "music_reactivity",
  "sensitivity",
  "beat_sensitivity",
  "sparking",
  "variation",
  "color_spread",
  "decay",
])

const HIGHER_IS_MORE_SUBDUED = new Set(["cooling", "fade_time"])
const PRESETTABLE = new Set([
  ...HIGHER_IS_MORE_ENERGETIC,
  ...HIGHER_IS_MORE_SUBDUED,
])

function adjustedValue(
  option: AnimationSettingOption,
  preset: Exclude<AnimationPreset, "balanced">,
) {
  if (
    option.type !== "number"
    || typeof option.default !== "number"
    || option.minimum === null
    || option.minimum === undefined
    || option.maximum === null
    || option.maximum === undefined
    || option.step === null
    || option.step === undefined
  ) return option.default

  const higherIsMore = HIGHER_IS_MORE_ENERGETIC.has(option.id)
  const higherIsSubdued = HIGHER_IS_MORE_SUBDUED.has(option.id)
  if (!higherIsMore && !higherIsSubdued) return option.default

  const towardMaximum = preset === "energetic" ? higherIsMore : higherIsSubdued
  const bound = towardMaximum ? option.maximum : option.minimum
  const value = option.default + (bound - option.default) * 0.65
  const stepped = option.minimum + Math.round((value - option.minimum) / option.step) * option.step
  const bounded = Math.min(option.maximum, Math.max(option.minimum, stepped))
  return Number.isInteger(option.step) ? Math.round(bounded) : Number(bounded.toFixed(3))
}

/** Build a style preset from the selected animation's validated schema. */
export function applyAnimationPreset(
  options: AnimationSettingOption[],
  current: Record<string, string | number | boolean>,
  preset: AnimationPreset,
) {
  const next = { ...current }
  for (const option of options) {
    if (!PRESETTABLE.has(option.id)) continue
    if (preset === "balanced") {
      next[option.id] = option.default
    } else {
      next[option.id] = adjustedValue(option, preset)
    }
  }
  return next
}
