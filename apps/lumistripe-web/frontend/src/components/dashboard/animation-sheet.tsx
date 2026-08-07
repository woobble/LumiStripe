import { useMemo, useState } from "react"
import { CheckIcon, SearchIcon, SparklesIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import type { AnimationOption } from "@/lib/api"

interface AnimationSheetProps {
  animations: AnimationOption[]
  current: string
  disabled: boolean
  onSelect: (name: string) => Promise<boolean>
}

function displayName(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ")
}

export function AnimationSheet({ animations, current, disabled, onSelect }: AnimationSheetProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
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

  const select = async (name: string) => {
    if (await onSelect(name)) {
      setOpen(false)
      setQuery("")
    }
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <Button
            variant="outline"
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
          <SheetTitle>Choose animation</SheetTitle>
          <SheetDescription>Selecting an animation switches playback to Static mode.</SheetDescription>
        </SheetHeader>
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
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-[max(1rem,env(safe-area-inset-bottom))]">
          {filteredAnimations.length === 0 ? (
            <p className="px-4 py-12 text-center text-sm text-muted-foreground">No animations match “{query}”.</p>
          ) : (
            <div className="space-y-1">
              {filteredAnimations.map((animation) => {
                const selected = animation.name === current
                return (
                  <button
                    key={animation.name}
                    type="button"
                    className="flex min-h-14 w-full items-center gap-3 rounded-xl px-3 py-2 text-left transition-colors hover:bg-white/5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50"
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
                )
              })}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
