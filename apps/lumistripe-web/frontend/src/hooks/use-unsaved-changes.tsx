import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react"
import { Link, NavLink, useBlocker } from "react-router"
import type { NavLinkProps, LinkProps } from "react-router"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"

type GuardContextValue = {
  dirty: boolean
  description: string
  register: (id: string, dirty: boolean, description?: string) => void
  unregister: (id: string) => void
}

const defaultUnsavedDescription = "Your Setup changes will be discarded if you leave this page."
const GuardContext = createContext<GuardContextValue | null>(null)

export function UnsavedChangesProvider({ children }: { children: ReactNode }) {
  const [registrations, setRegistrations] = useState<Record<string, string>>({})
  const dirty = Object.keys(registrations).length > 0
  const descriptions = [...new Set(Object.values(registrations))]
  const description = descriptions.length > 0 ? descriptions.join(" ") : defaultUnsavedDescription
  const blocker = useBlocker(useCallback(({ currentLocation, nextLocation }) => (
    dirty && (
      currentLocation.pathname !== nextLocation.pathname
      || currentLocation.search !== nextLocation.search
      || currentLocation.hash !== nextLocation.hash
    )
  ), [dirty]))
  const proceedingRef = useRef(false)

  useEffect(() => {
    if (blocker.state !== "blocked") proceedingRef.current = false
  }, [blocker.state])

  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (!dirty) return
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [dirty])

  const register = useCallback((id: string, nextDirty: boolean, nextDescription?: string) => {
    setRegistrations((current) => {
      if (nextDirty) {
        const value = nextDescription ?? defaultUnsavedDescription
        if (current[id] === value) return current
        return { ...current, [id]: value }
      }
      if (!(id in current)) return current
      const next = { ...current }
      delete next[id]
      return next
    })
  }, [])
  const unregister = useCallback((id: string) => {
    setRegistrations((current) => {
      if (!(id in current)) return current
      const next = { ...current }
      delete next[id]
      return next
    })
  }, [])

  const value = useMemo(() => ({
    dirty,
    description,
    register,
    unregister,
  }), [description, dirty, register, unregister])
  return (
    <GuardContext.Provider value={value}>
      {children}
      <AlertDialog
        open={blocker.state === "blocked"}
        onOpenChange={(open) => {
          if (!open && !proceedingRef.current && blocker.state === "blocked") blocker.reset()
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Leave without saving?</AlertDialogTitle>
            <AlertDialogDescription>{description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Stay</AlertDialogCancel>
            <AlertDialogAction onClick={() => {
              if (blocker.state === "blocked") {
                proceedingRef.current = true
                blocker.proceed()
              }
            }}>Leave</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </GuardContext.Provider>
  )
}

export function useUnsavedChangesGuard(isDirty: boolean, description?: string, enabled = true) {
  const context = useContext(GuardContext)
  const id = useId()
  const register = context?.register
  const unregister = context?.unregister
  useEffect(() => {
    if (!register || !unregister || !enabled) {
      unregister?.(id)
      return
    }
    register(id, isDirty, description)
    return () => unregister(id)
  }, [description, enabled, id, isDirty, register, unregister])
  return {
    dirty: isDirty,
  }
}

export function GuardedNavLink(props: NavLinkProps) {
  return <NavLink {...props} />
}

export function GuardedLink(props: LinkProps) {
  return <Link {...props} />
}
