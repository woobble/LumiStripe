import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { DashboardState } from "@/lib/api"

export function StripeTargetSelector({
  state,
  value,
  onChange,
}: {
  state: DashboardState
  value: string
  onChange: (target: string) => void
}) {
  const outputs = state.stripe_topology.outputs
  if (state.stripe_topology.layout !== "independent" || outputs.length === 0) return null

  const selectedName = value === "all"
    ? "All stripes"
    : outputs.find((stripe) => stripe.id === value)?.name ?? "Stripe"

  return (
    <section className="space-y-2" aria-labelledby="control-target-label">
      <div className="flex items-center justify-between gap-3 px-1 text-xs">
        <span id="control-target-label" className="font-medium text-muted-foreground">Control target</span>
        <span className="max-w-[60%] truncate font-medium text-foreground">{selectedName}</span>
      </div>
      <div className="overflow-x-auto pb-1">
        <ToggleGroup
          value={[value]}
          onValueChange={(values) => {
            if (values[0]) onChange(values[0])
          }}
          variant="outline"
          spacing={1}
          className="grid w-full gap-1 rounded-xl border border-border/70 bg-muted/40 p-1"
          style={{ gridTemplateColumns: `repeat(${outputs.length + 1}, minmax(0, 1fr))` }}
          aria-label="Control target"
        >
          <ToggleGroupItem value="all" className="h-11 min-w-0 rounded-lg border-0 px-2 data-pressed:bg-primary/15 data-pressed:text-primary-foreground">All</ToggleGroupItem>
          {outputs.map((stripe) => (
            <ToggleGroupItem
              key={stripe.id}
              value={stripe.id}
              title={stripe.name}
              className="h-11 min-w-0 truncate rounded-lg border-0 px-2 data-pressed:bg-primary/15 data-pressed:text-primary-foreground"
            >
              <span className="truncate">{stripe.name}</span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
    </section>
  )
}
