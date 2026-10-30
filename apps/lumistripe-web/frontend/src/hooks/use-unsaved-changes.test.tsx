import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import { createMemoryRouter, Link, Route, Routes, RouterProvider, useNavigate } from "react-router"
import { describe, expect, it } from "vitest"

import { UnsavedChangesProvider, useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"

function DirtyForm({ label }: { label: string }) {
  const [dirty, setDirty] = useState(true)
  useUnsavedChangesGuard(dirty, `${label} changes are unsaved.`)
  return <button type="button" onClick={() => setDirty(false)}>Save {label}</button>
}

function TestPage() {
  const [showSource, setShowSource] = useState(true)
  const navigate = useNavigate()
  return (
    <>
      <p>Current page</p>
      {showSource && <DirtyForm label="Source" />}
      <button type="button" onClick={() => setShowSource(false)}>Remove Source form</button>
      <DirtyForm label="Tuning" />
      <button type="button" onClick={() => navigate("/target")}>Navigate programmatically</button>
      <button type="button" onClick={() => navigate(-1)}>Go back</button>
      <Link to="/target">Open target</Link>
    </>
  )
}

function AppUnderTest() {
  return <UnsavedChangesProvider>
    <Routes>
      <Route path="/previous" element={<p>Previous page</p>} />
      <Route path="/form" element={<TestPage />} />
      <Route path="/target" element={<p>Target page</p>} />
    </Routes>
  </UnsavedChangesProvider>
}

function renderApp() {
  const router = createMemoryRouter([{ path: "*", element: <AppUnderTest /> }], {
    initialEntries: ["/previous", "/form"],
    initialIndex: 1,
  })
  return render(<RouterProvider router={router} />)
}

describe("independent unsaved-change registrations", () => {
  it("keeps navigation guarded when one of several dirty forms is saved", async () => {
    const user = userEvent.setup()
    renderApp()
    await user.click(screen.getByRole("button", { name: "Save Source" }))
    await user.click(screen.getByRole("button", { name: "Remove Source form" }))

    const unload = new Event("beforeunload", { cancelable: true })
    fireEvent(window, unload)
    expect(unload.defaultPrevented).toBe(true)

    await user.click(screen.getByRole("button", { name: "Navigate programmatically" }))
    expect(await screen.findByText("Tuning changes are unsaved.")).toBeInTheDocument()
    expect(screen.getByText("Current page")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Stay" }))
    expect(screen.getByText("Current page")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Go back" }))
    expect(await screen.findByText("Leave without saving?")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Stay" }))
    expect(screen.getByText("Current page")).toBeInTheDocument()

    await user.click(screen.getByRole("link", { name: "Open target" }))
    await user.click(await screen.findByRole("button", { name: "Leave" }))
    expect(await screen.findByText("Target page")).toBeInTheDocument()
  })
})
