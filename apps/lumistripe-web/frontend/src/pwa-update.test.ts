import { describe, expect, it, vi } from "vitest"

import { reloadLatestFrontend } from "@/pwa-update"

describe("reloadLatestFrontend", () => {
  it("checks for an update and applies a waiting service worker", async () => {
    const update = vi.fn().mockResolvedValue(undefined)
    const reload = vi.fn()
    const registration = {
      update: vi.fn().mockResolvedValue(undefined),
      installing: null,
      waiting: {} as ServiceWorker,
    } as unknown as ServiceWorkerRegistration
    const serviceWorkers = {
      getRegistration: vi.fn().mockResolvedValue(registration),
    } as unknown as ServiceWorkerContainer

    await reloadLatestFrontend(update, serviceWorkers, reload)

    expect(serviceWorkers.getRegistration).toHaveBeenCalledOnce()
    expect(registration.update).toHaveBeenCalledOnce()
    expect(update).toHaveBeenCalledWith(true)
    expect(reload).not.toHaveBeenCalled()
  })

  it("reloads normally when service workers are unavailable", async () => {
    const update = vi.fn().mockResolvedValue(undefined)
    const reload = vi.fn()

    await reloadLatestFrontend(update, undefined, reload)

    expect(update).not.toHaveBeenCalled()
    expect(reload).toHaveBeenCalledOnce()
  })

  it("reloads normally when checking for a service worker update fails", async () => {
    const update = vi.fn().mockResolvedValue(undefined)
    const reload = vi.fn()
    const registration = {
      update: vi.fn().mockRejectedValue(new Error("Update check failed")),
      installing: null,
      waiting: null,
    } as unknown as ServiceWorkerRegistration
    const serviceWorkers = {
      getRegistration: vi.fn().mockResolvedValue(registration),
    } as unknown as ServiceWorkerContainer

    await reloadLatestFrontend(update, serviceWorkers, reload)

    expect(update).not.toHaveBeenCalled()
    expect(reload).toHaveBeenCalledOnce()
  })
})
