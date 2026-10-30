import { memo, useState } from "react"
import { HexColorPicker } from "react-colorful"

import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"

const solidPresets = ["#EF4444", "#F59E0B", "#22C55E", "#06B6D4", "#3B82F6", "#8B5CF6"]

export const ColorControl = memo(function ColorControl({
  color,
  disabled,
  label,
  onCommit,
}: {
  color: string
  disabled: boolean
  label: string
  onCommit: (color: string) => Promise<boolean>
}) {
  const [colorDraft, setColorDraft] = useState<string | null>(null)
  const [colorOpen, setColorOpen] = useState(false)

  const commit = async (nextColor: string) => {
    setColorDraft(nextColor)
    const ok = await onCommit(nextColor)
    if (ok) setColorDraft(null)
    return ok
  }

  return (
    <section className="space-y-3 rounded-xl border border-border/70 bg-muted/30 p-3" aria-label={label}>
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">{label}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">Choose a color or use a preset.</p>
        </div>
        <Popover open={colorOpen} onOpenChange={setColorOpen}>
          <PopoverTrigger
            disabled={disabled}
            aria-label={`Choose ${label.toLowerCase()}`}
            className="size-11 shrink-0 rounded-full border-2 border-foreground/25 shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            style={{ backgroundColor: colorDraft ?? color }}
          />
          <PopoverContent className="w-64 space-y-3">
            <HexColorPicker color={colorDraft ?? color} onChange={setColorDraft} />
            <Button className="w-full" disabled={disabled} onClick={() => void commit(colorDraft ?? color).then((ok) => { if (ok) setColorOpen(false) })}>
              Apply color
            </Button>
          </PopoverContent>
        </Popover>
      </div>
      <div className="grid grid-cols-6 gap-2" aria-label={`${label} presets`}>
        {solidPresets.map((preset) => (
          <button
            key={preset}
            type="button"
            aria-label={`Set ${label.toLowerCase()} ${preset}`}
            disabled={disabled}
            onClick={() => void commit(preset)}
            className="aspect-square min-h-10 cursor-pointer rounded-full border border-foreground/25 transition-transform active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            style={{ backgroundColor: preset }}
          />
        ))}
      </div>
    </section>
  )
})
