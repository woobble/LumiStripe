type UpdateServiceWorker = (reloadPage?: boolean) => Promise<void>

const INSTALL_TIMEOUT_MS = 30_000

function waitForInstall(worker: ServiceWorker): Promise<boolean> {
  if (worker.state === "installed") return Promise.resolve(true)
  if (worker.state === "redundant") return Promise.resolve(false)

  return new Promise((resolve) => {
    const finish = (installed: boolean) => {
      window.clearTimeout(timeout)
      worker.removeEventListener("statechange", onStateChange)
      resolve(installed)
    }
    const onStateChange = () => {
      if (worker.state === "installed") finish(true)
      else if (worker.state === "redundant") finish(false)
    }
    const timeout = window.setTimeout(() => finish(false), INSTALL_TIMEOUT_MS)
    worker.addEventListener("statechange", onStateChange)
    onStateChange()
  })
}

export async function reloadLatestFrontend(
  updateServiceWorker: UpdateServiceWorker,
  serviceWorkers: ServiceWorkerContainer | undefined =
    typeof navigator === "undefined" ? undefined : navigator.serviceWorker,
  reload: () => void = () => window.location.reload(),
): Promise<void> {
  if (!serviceWorkers) {
    reload()
    return
  }

  try {
    const registration = await serviceWorkers.getRegistration()
    if (!registration) {
      reload()
      return
    }

    await registration.update()

    const installingWorker = registration.installing
    if (installingWorker && !(await waitForInstall(installingWorker))) {
      reload()
      return
    }

    if (registration.waiting) {
      await updateServiceWorker(true)
      return
    }
  } catch {
    // A failed update check should still let the user reload the current page.
  }

  reload()
}
