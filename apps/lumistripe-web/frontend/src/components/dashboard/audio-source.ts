export const audioSources = ["auto", "spotify", "bluetooth", "mic", "demo", "off"] as const
export type AudioSourceValue = typeof audioSources[number]

export const audioSourceLabels: Record<AudioSourceValue, string> = {
  auto: "Automatic",
  spotify: "Spotify Connect",
  bluetooth: "Bluetooth input",
  mic: "Microphone",
  demo: "Demo beat",
  off: "Off",
}

export function isAudioSource(value: string): value is AudioSourceValue {
  return (audioSources as readonly string[]).includes(value)
}

export function audioSourceLabel(value: string | null | undefined) {
  return value && isAudioSource(value) ? audioSourceLabels[value] : "Unavailable"
}
