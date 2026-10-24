import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode, type MouseEvent } from "react"
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
  setDirty: (dirty: boolean, description?: string) => void
  requestNavigation: (to: string) => boolean
}

const defaultUnsavedDescription = "Your Setup changes will be discarded if you leave this page."
const GuardContext = createContext<GuardContextValue | null>(null)

export function UnsavedChangesProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const [dirty, setDirty] = useState(false)
  const [description, setDescription] = useState(defaultUnsavedDescription)
  const [pendingPath, setPendingPath] = useState<string | null>(null)

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

  const updateDirty = useCallback((nextDirty: boolean, nextDescription?: string) => {
    setDirty(nextDirty)
    if (nextDirty) setDescription(nextDescription ?? defaultUnsavedDescription)
  }, [])

  const value = useMemo(() => ({
    dirty,
    description,
    setDirty: updateDirty,
    requestNavigation,
  }), [description, dirty, requestNavigation, updateDirty])
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

export function useUnsavedChangesGuard(isDirty: boolean, description?: string) {
  const context = useContext(GuardContext)
  const setDirty = context?.setDirty
  useEffect(() => {
    if (!setDirty) return
    setDirty(isDirty, description)
    return () => setDirty(false)
  }, [description, isDirty, setDirty])
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
