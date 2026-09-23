import { lazy, Suspense, type ReactNode } from "react"
import { Navigate, Route, Routes } from "react-router"

import type { AccessController } from "@/hooks/use-access"
import type { DashboardController } from "@/hooks/use-dashboard"
import { SetupPage } from "@/components/dashboard/setup-nav"

const AudioTuningPanel = lazy(async () => {
  const module = await import("@/components/dashboard/audio-tuning-panel")
  return { default: module.AudioTuningPanel }
})
const CalibrationPanel = lazy(async () => {
  const module = await import("@/components/dashboard/calibration-panel")
  return { default: module.CalibrationPanel }
})
const ControlPanel = lazy(async () => {
  const module = await import("@/components/dashboard/control-panel")
  return { default: module.ControlPanel }
})
const AudioStatusPanel = lazy(async () => {
  const module = await import("@/components/dashboard/audio-status-panel")
  return { default: module.AudioStatusPanel }
})
const AudioInputPanel = lazy(async () => {
  const module = await import("@/components/dashboard/audio-input-panel")
  return { default: module.AudioInputPanel }
})
const StartupPanel = lazy(async () => {
  const module = await import("@/components/dashboard/startup-panel")
  return { default: module.StartupPanel }
})
const StatusPanel = lazy(async () => {
  const module = await import("@/components/dashboard/status-panel")
  return { default: module.StatusPanel }
})
const StripeManagementPanel = lazy(async () => {
  const module = await import("@/components/dashboard/stripe-management-panel")
  return { default: module.StripeManagementPanel }
})

function SetupGuard({ access, children }: { access: AccessController; children: ReactNode }) {
  if (access.loading) return children
  if (access.required && !access.authenticated) return <Navigate to="/" replace />
  return children
}

export function DashboardRoutes({
  access,
  controller,
  fallback,
}: {
  access: AccessController
  controller: DashboardController
  fallback: ReactNode
}) {
  return (
    <Routes>
      <Route path="/" element={<Suspense fallback={fallback}><ControlPanel controller={controller} /></Suspense>} />
      <Route path="/audio" element={<Suspense fallback={fallback}><AudioStatusPanel /></Suspense>} />
      <Route path="/setup" element={<Navigate to="/setup/stripes" replace />} />
      <Route path="/setup/stripes" element={<SetupGuard access={access}><Suspense fallback={fallback}><StripeManagementPanel controller={controller} /></Suspense></SetupGuard>} />
      <Route path="/setup/color" element={<SetupGuard access={access}><SetupPage><Suspense fallback={fallback}><CalibrationPanel controller={controller} /></Suspense></SetupPage></SetupGuard>} />
      <Route path="/setup/audio" element={<SetupGuard access={access}><Suspense fallback={fallback}><AudioInputPanel /></Suspense></SetupGuard>} />
      <Route path="/setup/audio/tuning" element={<SetupGuard access={access}><Suspense fallback={fallback}><AudioTuningPanel /></Suspense></SetupGuard>} />
      <Route path="/setup/startup" element={<SetupGuard access={access}><Suspense fallback={fallback}><StartupPanel /></Suspense></SetupGuard>} />
      <Route path="/calibration" element={<Navigate to="/setup/color" replace />} />
      <Route path="/diagnostics" element={<Suspense fallback={fallback}><StatusPanel controller={controller} onLogout={access.required && access.authenticated ? access.logout : undefined} /></Suspense>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
