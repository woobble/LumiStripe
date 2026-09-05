import { describe, expect, it } from "vitest"

import { buildPreviewLayout, decodePreviewFrame } from "@/lib/preview"
import { initialState } from "@/test/fixtures"

function packet(sequence: number, outputs: Uint8Array[]) {
  const size = 12 + outputs.reduce((total, output) => total + 4 + output.byteLength, 0)
  const buffer = new ArrayBuffer(size)
  const view = new DataView(buffer)
  new Uint8Array(buffer, 0, 4).set([0x4c, 0x53, 0x46, 0x50])
  view.setUint8(4, 1)
  view.setUint32(6, sequence, true)
  view.setUint16(10, outputs.length, true)
  let offset = 12
  for (const output of outputs) {
    view.setUint32(offset, output.byteLength / 4, true)
    offset += 4
    new Uint8Array(buffer, offset, output.byteLength).set(output)
    offset += output.byteLength
  }
  return buffer
}

describe("preview protocol and layout", () => {
  it("decodes a binary RGBA frame", async () => {
    const first = new Uint8Array([255, 0, 1, 255])
    const second = new Uint8Array([2, 3, 4, 128, 9, 8, 7, 0])

    await expect(decodePreviewFrame(packet(23, [first, second]))).resolves.toEqual({
      sequence: 23,
      outputs: [first, second],
    })
  })

  it("rejects malformed and unsupported packets", async () => {
    await expect(decodePreviewFrame(new ArrayBuffer(12))).resolves.toBeNull()
    await expect(decodePreviewFrame(packet(1, [new Uint8Array([1, 2, 3])]))).resolves.toBeNull()
  })

  it("keeps continuous output boundaries in pixel order", () => {
    const layout = buildPreviewLayout({
      ...initialState.stripe_topology,
      layout: "continuous",
      outputs: [
        { ...initialState.stripe_topology.outputs[0], id: "left", name: "Left", pixels: 2 },
        { ...initialState.stripe_topology.outputs[0], id: "right", name: "Right", pixels: 3 },
      ],
    })

    expect(layout.strips).toHaveLength(2)
    expect(layout.strips[0].x).toBeLessThan(layout.strips[1].x)
    expect(layout.strips[0].width + layout.strips[1].width).toBeLessThan(0.88)
    expect(layout.strips.map((strip) => strip.pixelCount)).toEqual([2, 3])
  })
})
