import { CheckCircle2Icon, CircleAlertIcon, RefreshCwIcon, TriangleAlertIcon } from "lucide-react"
import { Link } from "react-router"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import type { AudioDiagnostic } from "@/components/dashboard/audio-diagnostics-model"

export function AudioDiagnostics({
  issues,
  onRetrySettings,
  onRetryOutput,
}: {
  issues: AudioDiagnostic[]
  onRetrySettings: () => void
  onRetryOutput: () => void
}) {
  const warningCount = issues.filter((issue) => issue.severity === "warning").length
  const hasWarnings = warningCount > 0
  const description = hasWarnings
    ? `${warningCount} issue${warningCount === 1 ? "" : "s"} need attention.`
    : issues.length > 0
      ? "Audio status is still being checked."
      : "No input or output problems detected."
  return (
    <Card className="border-white/5 bg-card/80" aria-label="Audio health">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2"><AudioHealthIcon hasWarnings={hasWarnings} />Audio health</CardTitle>
          <BadgeText hasWarnings={hasWarnings} issues={issues} />
        </div>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      {issues.length > 0 && (
        <CardContent className="space-y-3">
          {issues.map((issue) => (
            <div key={issue.id} className={issue.severity === "warning" ? "rounded-xl border border-amber-400/20 bg-amber-500/10 p-3" : "rounded-xl border border-sky-400/20 bg-sky-500/10 p-3"}>
              <div className="flex items-start gap-2.5">
                {issue.severity === "warning" ? <TriangleAlertIcon className="mt-0.5 size-4 shrink-0 text-amber-300" aria-hidden="true" /> : <CircleAlertIcon className="mt-0.5 size-4 shrink-0 text-sky-300" aria-hidden="true" />}
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{issue.title}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{issue.message}</p>
                  {issue.action?.type === "link" && <Link className="mt-2 inline-flex min-h-9 items-center text-sm font-medium text-cyan-200 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" to={issue.action.to}>{issue.action.label}</Link>}
                  {issue.action?.type === "retry-settings" && <Button type="button" variant="outline" size="sm" className="mt-2" onClick={onRetrySettings}><RefreshCwIcon />{issue.action.label}</Button>}
                  {issue.action?.type === "retry-output" && <Button type="button" variant="outline" size="sm" className="mt-2" onClick={onRetryOutput}><RefreshCwIcon />{issue.action.label}</Button>}
                  {issue.action?.type === "output-controls" && <a className="mt-2 inline-flex min-h-9 items-center text-sm font-medium text-cyan-200 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" href="#audio-output-controls">{issue.action.label}</a>}
                </div>
              </div>
            </div>
          ))}
        </CardContent>
      )}
    </Card>
  )
}

function AudioHealthIcon({ hasWarnings }: { hasWarnings: boolean }) {
  return hasWarnings ? <TriangleAlertIcon className="size-4 text-amber-300" aria-hidden="true" /> : <CheckCircle2Icon className="size-4 text-emerald-300" aria-hidden="true" />
}

function BadgeText({ hasWarnings, issues }: { hasWarnings: boolean; issues: AudioDiagnostic[] }) {
  return <span className={hasWarnings ? "rounded-full border border-amber-300/20 bg-amber-500/10 px-2 py-1 text-xs text-amber-100" : "rounded-full border border-emerald-300/20 bg-emerald-500/10 px-2 py-1 text-xs text-emerald-100"}>{hasWarnings ? `${issues.filter((issue) => issue.severity === "warning").length} warnings` : issues.length ? "Starting" : "Healthy"}</span>
}
