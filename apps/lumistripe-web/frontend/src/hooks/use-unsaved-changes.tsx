import { createContext, useCallback, useContext, useEffect, useId, useMemo, useState, type ReactNode, type MouseEvent } from "react"
import { Link, NavLink, useNavigate } from "react-router"
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
  requestNavigation: (to: string) => boolean
}

const defaultUnsavedDescription = "Your Setup changes will be discarded if you leave this page."
const GuardContext = createContext<GuardContextValue | null>(null)

export function UnsavedChangesProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const [registrations, setRegistrations] = useState<Record<string, string>>({})
  const [pendingPath, setPendingPath] = useState<string | null>(null)
  const dirty = Object.keys(registrations).length > 0
  const descriptions = [...new Set(Object.values(registrations))]
  const description = descriptions.length > 0 ? descriptions.join(" ") : defaultUnsavedDescription

  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (!dirty) return
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [dirty])

  const requestNavigation = useCallback((to: string) => {
    if (!dirty) return false
    setPendingPath(to)
    return true
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
    requestNavigation,
  }), [description, dirty, register, requestNavigation, unregister])
  return (
    <GuardContext.Provider value={value}>
      {children}
      <AlertDialog open={pendingPath !== null} onOpenChange={(open) => { if (!open) setPendingPath(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Leave without saving?</AlertDialogTitle>
            <AlertDialogDescription>{description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Stay</AlertDialogCancel>
            <AlertDialogAction onClick={() => { const path = pendingPath; setPendingPath(null); if (path) navigate(path) }}>Leave</AlertDialogAction>
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
    requestNavigation: context?.requestNavigation ?? (() => false),
  }
}

export function GuardedNavLink({ onClick, ...props }: NavLinkProps) {
  const context = useContext(GuardContext)
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event)
    if (event.defaultPrevented || !context?.dirty || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    const href = event.currentTarget.getAttribute("href")
    if (!href || href === window.location.pathname) return
    if (context.requestNavigation(href)) event.preventDefault()
  }
  return <NavLink {...props} onClick={handleClick} />
}

export function GuardedLink({ onClick, ...props }: LinkProps) {
  const context = useContext(GuardContext)
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event)
    if (event.defaultPrevented || !context?.dirty || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    const href = event.currentTarget.getAttribute("href")
    if (href && context.requestNavigation(href)) event.preventDefault()
  }
  return <Link {...props} onClick={handleClick} />
}
