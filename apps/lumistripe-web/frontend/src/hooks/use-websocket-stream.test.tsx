import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { useWebSocketStream } from "@/hooks/use-websocket-stream"

class MockWebSocket {
  static instances: MockWebSocket[] = []
  binaryType = "blob"
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  close = vi.fn()

  constructor(_url: string) {
    MockWebSocket.instances.push(this)
  }

  emit(data: string) {
    this.onmessage?.(new MessageEvent("message", { data }))
  }

  disconnect() {
    this.onclose?.(new CloseEvent("close", { code: 1006 }))
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  MockWebSocket.instances = []
  vi.stubGlobal("WebSocket", MockWebSocket)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it("keeps the newest message when asynchronous parsing finishes out of order", async () => {
  const first = deferred<string>()
  const second = deferred<string>()
  const parse = (data: unknown) => data === "first" ? first.promise : second.promise
  const { result } = renderHook(() => useWebSocketStream({ url: "ws://test", parse }))
  const socket = MockWebSocket.instances[0]
  act(() => { socket.emit("first"); socket.emit("second") })
  await act(async () => { second.resolve("new"); await second.promise })
  await act(async () => { first.resolve("old"); await first.promise })
  expect(result.current.data).toBe("new")
})

it("ignores results and failures from a disconnected socket", async () => {
  vi.useFakeTimers()
  const pending = deferred<string>()
  const failed = deferred<string>()
  const parse = (data: unknown) => data === "pending" ? pending.promise : failed.promise
  const { result } = renderHook(() => useWebSocketStream({ url: "ws://test", parse }))
  const old = MockWebSocket.instances[0]
  act(() => { old.emit("pending"); old.emit("failed"); old.disconnect() })
  act(() => vi.advanceTimersByTime(1000))
  const replacement = MockWebSocket.instances[1]
  expect(replacement).toBeDefined()
  await act(async () => { pending.resolve("stale"); await pending.promise })
  await act(async () => { failed.reject(new Error("late failure")); await failed.promise.catch(() => {}) })
  expect(result.current.data).toBeNull()
  expect(replacement.close).not.toHaveBeenCalled()
})

it("does not apply pending data after a parse failure starts closing the socket", async () => {
  const pending = deferred<string>()
  const parse = (data: unknown) => {
    if (data === "bad") throw new Error("invalid")
    return pending.promise
  }
  const { result } = renderHook(() => useWebSocketStream({ url: "ws://test", parse }))
  const socket = MockWebSocket.instances[0]
  act(() => { socket.emit("pending"); socket.emit("bad") })
  expect(socket.close).toHaveBeenCalledOnce()
  await act(async () => { pending.resolve("stale"); await pending.promise })
  expect(result.current.data).toBeNull()
})
