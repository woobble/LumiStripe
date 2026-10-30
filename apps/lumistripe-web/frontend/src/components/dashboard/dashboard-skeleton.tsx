import { Skeleton } from "@/components/ui/skeleton"

function DashboardSkeleton() {
  return (
    <div
      role="status"
      aria-label="Loading dashboard"
      aria-busy="true"
      className="w-full space-y-5"
    >
      <header className="flex items-center justify-between gap-4">
        <div className="space-y-2">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-8 w-36 sm:w-44" />
        </div>
        <Skeleton className="h-9 w-24 rounded-full sm:w-28" />
      </header>

      <Skeleton className="h-12 w-full rounded-xl" />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(19rem,0.8fr)]">
        <div className="space-y-4">
          <section className="space-y-4 rounded-2xl border border-border/70 bg-card p-4 sm:p-5">
            <div className="flex items-center justify-between gap-4">
              <div className="space-y-2">
                <Skeleton className="h-5 w-28" />
                <Skeleton className="h-3 w-40 max-w-full" />
              </div>
              <Skeleton className="h-9 w-14" />
            </div>
            <Skeleton className="h-2 w-full rounded-full" />
            <div className="flex items-center justify-between gap-4 border-t border-border/60 pt-3">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-10 w-28 rounded-xl" />
            </div>
          </section>

          <section className="space-y-4 rounded-2xl border border-border/70 bg-card p-4 sm:p-5">
            <div className="flex items-center justify-between gap-4">
              <Skeleton className="h-5 w-32" />
              <Skeleton className="h-9 w-24 rounded-lg" />
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {Array.from({ length: 4 }, (_, index) => (
                <Skeleton key={index} className="h-12 rounded-xl" />
              ))}
            </div>
            <Skeleton className="h-36 w-full rounded-xl" />
          </section>
        </div>

        <aside className="space-y-4">
          <section className="flex min-h-36 flex-col justify-between gap-4 rounded-2xl border border-border/70 bg-card p-4 sm:p-5">
            <div className="space-y-3">
              <Skeleton className="h-5 w-28" />
              <Skeleton className="h-4 w-full max-w-64" />
            </div>
            <Skeleton className="h-10 w-full rounded-xl" />
          </section>
          <section className="space-y-4 rounded-2xl border border-border/70 bg-card p-4 sm:p-5">
            <Skeleton className="h-5 w-28" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </section>
        </aside>
      </div>
    </div>
  )
}

export { DashboardSkeleton }
