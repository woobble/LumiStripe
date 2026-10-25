import { describe, expect, it } from "vitest"

import { applyAnimationPreset } from "@/components/dashboard/animation-presets"
import type { AnimationSettingOption } from "@/lib/api"

const options: AnimationSettingOption[] = [
  {
    id: "speed",
    label: "Speed",
    type: "number",
    default: 50,
    description: "Movement speed.",
    minimum: 0,
    maximum: 100,
    step: 1,
    unit: "%",
    choices: [],
    visible_when_option: null,
    visible_when_value: null,
  },
  {
    id: "intensity",
    label: "Intensity",
    type: "number",
    default: 80,
    description: "Brightness.",
    minimum: 0,
    maximum: 100,
    step: 1,
    unit: "%",
    choices: [],
    visible_when_option: null,
    visible_when_value: null,
  },
  {
    id: "cooling",
    label: "Cooling",
    type: "number",
    default: 40,
    description: "Heat dissipation.",
    minimum: 0,
    maximum: 100,
    step: 1,
    unit: "%",
    choices: [],
    visible_when_option: null,
    visible_when_value: null,
  },
  {
    id: "palette",
    label: "Palette",
    type: "enum",
    default: "rainbow",
    description: "Color palette.",
    minimum: null,
    maximum: null,
    step: null,
    unit: null,
    choices: ["rainbow", "neon", "custom"],
    visible_when_option: null,
    visible_when_value: null,
  },
]

describe("animation presets", () => {
  it("derives distinct bounded styles from the selected schema and preserves categorical choices", () => {
    const saved = { speed: 50, intensity: 80, cooling: 40, palette: "custom" }
    const subtle = applyAnimationPreset(options, saved, "subtle")
    const energetic = applyAnimationPreset(options, saved, "energetic")
    const balanced = applyAnimationPreset(options, subtle, "balanced")

    expect(subtle.speed).toBe(18)
    expect(subtle.intensity).toBe(28)
    expect(subtle.cooling).toBe(79)
    expect(energetic.speed).toBe(83)
    expect(energetic.intensity).toBe(93)
    expect(energetic.cooling).toBe(14)
    expect(balanced).toEqual(saved)
    expect(subtle.palette).toBe("custom")
    expect(energetic.palette).toBe("custom")
  })
})
