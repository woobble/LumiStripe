import type { StripeTopology } from "@/lib/api"

const PREVIEW_MAGIC = [0x4c, 0x53, 0x46, 0x50]
const PREVIEW_VERSION = 1
const PREVIEW_HEADER_BYTES = 12
const PREVIEW_OUTPUT_HEADER_BYTES = 4
const MAX_OUTPUTS = 2
const MAX_PIXELS = 4096

export interface PreviewFrame {
  sequence: number
  outputs: Uint8Array[]
}

export interface PreviewStripLayout {
  id: string
  name: string
  outputIndex: number
  pixelCount: number
  x: number
  y: number
  width: number
  height: number
}

export interface PreviewLayout {
  strips: PreviewStripLayout[]
}

export async function decodePreviewFrame(data: unknown): Promise<PreviewFrame | null> {
  let buffer: ArrayBuffer
  if (data instanceof ArrayBuffer) {
    buffer = data
  } else if (typeof Blob !== "undefined" && data instanceof Blob) {
    buffer = await data.arrayBuffer()
  } else {
    return null
  }

  if (buffer.byteLength < PREVIEW_HEADER_BYTES) return null
  const view = new DataView(buffer)
  if (PREVIEW_MAGIC.some((value, index) => view.getUint8(index) !== value)) return null
  if (view.getUint8(4) !== PREVIEW_VERSION) return null

  const outputCount = view.getUint16(10, true)
  if (outputCount > MAX_OUTPUTS) return null

  let offset = PREVIEW_HEADER_BYTES
  const outputs: Uint8Array[] = []
  for (let index = 0; index < outputCount; index += 1) {
    if (offset + PREVIEW_OUTPUT_HEADER_BYTES > buffer.byteLength) return null
    const pixelCount = view.getUint32(offset, true)
    offset += PREVIEW_OUTPUT_HEADER_BYTES
    const byteLength = pixelCount * 4
    if (pixelCount > MAX_PIXELS || offset + byteLength > buffer.byteLength) return null
    outputs.push(new Uint8Array(buffer, offset, byteLength).slice())
    offset += byteLength
  }
  if (offset !== buffer.byteLength) return null

  return {
    sequence: view.getUint32(6, true),
    outputs,
  }
}

export function buildPreviewLayout(topology: StripeTopology): PreviewLayout {
  const outputs = topology.outputs
  if (outputs.length === 0) return { strips: [] }

  const left = 0.06
  const width = 0.88
  if (topology.layout === "continuous") {
    const totalPixels = outputs.reduce((sum, output) => sum + output.pixels, 0)
    const gap = outputs.length > 1 ? 0.018 : 0
    const unit = (width - gap) / Math.max(totalPixels, 1)
    let cursor = 0
    return {
      strips: outputs.map((output, outputIndex) => {
        const strip = {
          id: output.id,
          name: output.name,
          outputIndex,
          pixelCount: output.pixels,
          x: left + cursor * unit + (outputIndex > 0 ? gap : 0),
          y: 0.29,
          width: output.pixels * unit,
          height: 0.42,
        }
        cursor += output.pixels
        return strip
      }),
    }
  }

  const rowGap = 0.1
  const rowHeight = outputs.length === 1 ? 0.42 : 0.27
  const totalHeight = outputs.length * rowHeight + (outputs.length - 1) * rowGap
  const top = (1 - totalHeight) / 2
  return {
    strips: outputs.map((output, outputIndex) => ({
      id: output.id,
      name: output.name,
      outputIndex,
      pixelCount: output.pixels,
      x: left,
      y: top + outputIndex * (rowHeight + rowGap),
      width,
      height: rowHeight,
    })),
  }
}

export function emptyPreviewFrame(topology: StripeTopology): PreviewFrame {
  return {
    sequence: 0,
    outputs: topology.outputs.map((output) => new Uint8Array(output.pixels * 4)),
  }
}
