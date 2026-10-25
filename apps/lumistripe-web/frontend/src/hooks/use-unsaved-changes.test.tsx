import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import { MemoryRouter, Route, Routes } from "react-router"
import { describe, expect, it } from "vitest"

import { GuardedLink, UnsavedChangesProvider, useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"

function DirtyForm({ label }: { label: string }) {
  const [dirty, setDirty] = useState(true)
  useUnsavedChangesGuard(dirty, `${label} changes are unsaved.`)
  return <button type="button" onClick={() => setDirty(false)}>Save {label}</button>
}

function TestPage() {
  const [showSource, setShowSource] = useState(true)
  return (
    <>
      <p>Current page</p>
      {showSource && <DirtyForm label="Source" />}
      <button type="button" onClick={() => setShowSource(false)}>Remove Source form</button>
      <DirtyForm label="Tuning" />
      <GuardedLink to="/target">Open target</GuardedLink>
    </>
  )
}

function AppUnderTest() {
  return (
    <MemoryRouter initialEntries={["/form"]}>
      <UnsavedChangesProvider>
        <Routes>
          <Route path="/form" element={<TestPage />} />
          <Route path="/target" element={<p>Target page</p>} />
        </Routes>
      </UnsavedChangesProvider>
    </MemoryRouter>
  )
}

describe("independent unsaved-change registrations", () => {
  it("keeps navigation guarded when one of several dirty forms is saved", async () => {
    const user = userEvent.setup()
    render(<AppUnderTest />)
    await user.click(screen.getByRole("button", { name: "Save Source" }))
    await user.click(screen.getByRole("button", { name: "Remove Source form" }))

    const unload = new Event("beforeunload", { cancelable: true })
    fireEvent(window, unload)
    expect(unload.defaultPrevented).toBe(true)

    await user.click(screen.getByRole("link", { name: "Open target" }))
    expect(await screen.findByText("Tuning changes are unsaved.")).toBeInTheDocument()
    expect(screen.getByText("Current page")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Stay" }))
    expect(screen.getByText("Current page")).toBeInTheDocument()
    await user.click(screen.getByRole("link", { name: "Open target" }))
    await user.click(await screen.findByRole("button", { name: "Leave" }))
    expect(await screen.findByText("Target page")).toBeInTheDocument()
  })
})
