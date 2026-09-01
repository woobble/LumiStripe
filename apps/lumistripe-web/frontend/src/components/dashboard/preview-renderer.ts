import type { StripeTopology } from "@/lib/api"
import { buildPreviewLayout, type PreviewFrame, type PreviewLayout, type PreviewStripLayout } from "@/lib/preview"

const RENDERER_BACKGROUND = [0.025, 0.018, 0.05, 1] as const
const GPU_BUFFER_USAGE_STORAGE = 0x80
const GPU_BUFFER_USAGE_COPY_DST = 0x08

function resizeCanvas(canvas: HTMLCanvasElement) {
  const devicePixelRatio = Math.min(window.devicePixelRatio || 1, 2)
  const width = Math.max(1, Math.floor((canvas.clientWidth || 640) * devicePixelRatio))
  const height = Math.max(1, Math.floor((canvas.clientHeight || 280) * devicePixelRatio))
  if (canvas.width !== width) canvas.width = width
  if (canvas.height !== height) canvas.height = height
  return { width, height }
}

function pixelColor(output: Uint8Array | undefined, index: number) {
  const offset = index * 4
  const alpha = (output?.[offset + 3] ?? 0) / 255
  return {
    red: output?.[offset] ?? 0,
    green: output?.[offset + 1] ?? 0,
    blue: output?.[offset + 2] ?? 0,
    alpha,
  }
}

function buildInstances(frame: PreviewFrame, layout: PreviewLayout) {
  const instances = new Float32Array(layout.strips.reduce((total, strip) => total + strip.pixelCount, 0) * 8)
  let offset = 0
  for (const strip of layout.strips) {
    const output = frame.outputs[strip.outputIndex]
    for (let index = 0; index < strip.pixelCount; index += 1) {
      const color = pixelColor(output, index)
      const position = index / Math.max(strip.pixelCount, 1)
      const cellGap = Math.min(0.18 / Math.max(strip.pixelCount, 1), 0.008)
      instances[offset] = strip.x + position * strip.width + cellGap
      instances[offset + 1] = strip.y + strip.height * 0.18
      instances[offset + 2] = Math.max(strip.width / Math.max(strip.pixelCount, 1) - cellGap * 2, 0.0001)
      instances[offset + 3] = strip.height * 0.64
      instances[offset + 4] = color.red / 255
      instances[offset + 5] = color.green / 255
      instances[offset + 6] = color.blue / 255
      instances[offset + 7] = color.alpha
      offset += 8
    }
  }
  return instances
}

export interface PreviewRenderer {
  render(frame: PreviewFrame, topology: StripeTopology): void
  resize(): void
  dispose(): void
}

export type WebGpuFailureReason =
  | "insecure-context"
  | "unsupported-browser"
  | "no-adapter"
  | "context-unavailable"
  | "initialization-failed"

export class CanvasPreviewRenderer implements PreviewRenderer {
  private readonly canvas: HTMLCanvasElement
  private context: CanvasRenderingContext2D | null = null

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
  }

  initialize() {
    try {
      this.context = this.canvas.getContext("2d")
    } catch {
      this.context = null
    }
    return this.context !== null
  }

  render(frame: PreviewFrame, topology: StripeTopology) {
    const context = this.context
    if (!context) return
    const { width, height } = resizeCanvas(this.canvas)
    const layout = buildPreviewLayout(topology)
    context.save()
    context.clearRect(0, 0, width, height)
    context.fillStyle = "rgb(7, 5, 14)"
    context.fillRect(0, 0, width, height)
    context.shadowBlur = Math.min(14, height * 0.05)
    for (const strip of layout.strips) {
      const output = frame.outputs[strip.outputIndex]
      for (let index = 0; index < strip.pixelCount; index += 1) {
        const color = pixelColor(output, index)
        if (color.alpha <= 0) continue
        const position = index / Math.max(strip.pixelCount, 1)
        const gap = Math.min(0.18 / Math.max(strip.pixelCount, 1), 0.008)
        const x = (strip.x + position * strip.width + gap) * width
        const y = (strip.y + strip.height * 0.18) * height
        const cellWidth = Math.max((strip.width / Math.max(strip.pixelCount, 1) - gap * 2) * width, 1)
        const cellHeight = Math.max(strip.height * 0.64 * height, 1)
        context.fillStyle = `rgb(${color.red} ${color.green} ${color.blue})`
        context.shadowColor = context.fillStyle
        context.globalAlpha = color.alpha
        context.fillRect(x, y, cellWidth, cellHeight)
      }
    }
    context.restore()
  }

  resize() {
    resizeCanvas(this.canvas)
  }

  dispose() {
    this.context = null
  }
}

export class WebGpuPreviewRenderer implements PreviewRenderer {
  private readonly canvas: HTMLCanvasElement
  private device: GPUDevice | null = null
  private context: GPUCanvasContext | null = null
  private pipeline: GPURenderPipeline | null = null
  private buffer: GPUBuffer | null = null
  private bindGroup: GPUBindGroup | null = null
  private format: GPUTextureFormat | null = null
  failureReason: WebGpuFailureReason | null = null

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
  }

  async initialize() {
    this.failureReason = null
    if (!window.isSecureContext) {
      this.failureReason = "insecure-context"
      return false
    }
    if (!("gpu" in navigator) || !navigator.gpu) {
      this.failureReason = "unsupported-browser"
      return false
    }
    try {
      const gpu = navigator.gpu
      const adapter = await gpu.requestAdapter()
      if (!adapter) {
        this.failureReason = "no-adapter"
        return false
      }
      const device = await adapter.requestDevice()
      this.device = device
      const context = this.canvas.getContext("webgpu") as GPUCanvasContext | null
      if (!context) {
        this.failureReason = "context-unavailable"
        this.dispose()
        return false
      }
      this.context = context
      this.format = gpu.getPreferredCanvasFormat()
      this.pipeline = device.createRenderPipeline({
        layout: "auto",
        vertex: {
          module: device.createShaderModule({ code: SHADER }),
          entryPoint: "vertexMain",
        },
        fragment: {
          module: device.createShaderModule({ code: SHADER }),
          entryPoint: "fragmentMain",
          targets: [{ format: this.format }],
        },
        primitive: { topology: "triangle-list" },
      })
      this.resize()
      return true
    } catch (error) {
      this.failureReason = "initialization-failed"
      console.warn("LumiStripe WebGPU preview initialization failed", error)
      this.dispose()
      return false
    }
  }

  render(frame: PreviewFrame, topology: StripeTopology) {
    if (!this.device || !this.context || !this.pipeline) return
    const instances = buildInstances(frame, buildPreviewLayout(topology))
    if (instances.byteLength > 0) {
      if (!this.buffer || this.buffer.size < instances.byteLength) {
        this.buffer?.destroy()
        this.buffer = this.device.createBuffer({
          size: instances.byteLength,
          usage: GPU_BUFFER_USAGE_STORAGE | GPU_BUFFER_USAGE_COPY_DST,
        })
        this.bindGroup = this.device.createBindGroup({
          layout: this.pipeline.getBindGroupLayout(0),
          entries: [{ binding: 0, resource: { buffer: this.buffer } }],
        })
      }
      this.device.queue.writeBuffer(this.buffer, 0, instances)
    }

    const encoder = this.device.createCommandEncoder()
    const pass = encoder.beginRenderPass({
      colorAttachments: [{
        view: this.context.getCurrentTexture().createView(),
        clearValue: { r: RENDERER_BACKGROUND[0], g: RENDERER_BACKGROUND[1], b: RENDERER_BACKGROUND[2], a: RENDERER_BACKGROUND[3] },
        loadOp: "clear",
        storeOp: "store",
      }],
    })
    pass.setPipeline(this.pipeline)
    if (instances.byteLength > 0 && this.bindGroup) {
      pass.setBindGroup(0, this.bindGroup)
      pass.draw(6, instances.length / 8)
    }
    pass.end()
    this.device.queue.submit([encoder.finish()])
  }

  resize() {
    if (!this.device || !this.context || !this.format) return
    resizeCanvas(this.canvas)
    this.context.configure({ device: this.device, format: this.format, alphaMode: "premultiplied" })
  }

  dispose() {
    this.buffer?.destroy()
    this.buffer = null
    this.bindGroup = null
    this.pipeline = null
    this.context = null
    this.device?.destroy()
    this.device = null
  }
}

export function webGpuFailureMessage(reason: WebGpuFailureReason | null) {
  switch (reason) {
    case "insecure-context":
      return "WebGPU needs HTTPS (localhost is also allowed)."
    case "unsupported-browser":
      return "This browser does not expose WebGPU. Try a current Chrome, Edge, or Safari."
    case "no-adapter":
      return "No compatible GPU adapter was found on this device."
    case "context-unavailable":
      return "The browser could not create a WebGPU canvas."
    case "initialization-failed":
      return "WebGPU initialization failed; check the browser console for details."
    default:
      return null
  }
}

export function previewLabels(topology: StripeTopology) {
  return buildPreviewLayout(topology).strips.map((strip: PreviewStripLayout) => ({
    ...strip,
    left: `${strip.x * 100}%`,
    top: `${Math.max(strip.y - 0.07, 0.02) * 100}%`,
  }))
}

const SHADER = /* wgsl */ `
struct Instance {
  x: f32,
  y: f32,
  width: f32,
  height: f32,
};

@group(0) @binding(0) var<storage, read> instances: array<vec4<f32>>;

struct VertexOutput {
  @builtin(position) position: vec4<f32>,
  @location(0) color: vec4<f32>,
};

@vertex
fn vertexMain(@builtin(vertex_index) vertexIndex: u32, @builtin(instance_index) instanceIndex: u32) -> VertexOutput {
  let corners = array<vec2<f32>, 6>(
    vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 0.0), vec2<f32>(0.0, 1.0),
    vec2<f32>(0.0, 1.0), vec2<f32>(1.0, 0.0), vec2<f32>(1.0, 1.0)
  );
  let rectangle = instances[instanceIndex * 2u];
  let color = instances[instanceIndex * 2u + 1u];
  let point = rectangle.xy + corners[vertexIndex] * rectangle.zw;
  var output: VertexOutput;
  output.position = vec4<f32>(point.x * 2.0 - 1.0, 1.0 - point.y * 2.0, 0.0, 1.0);
  output.color = color;
  return output;
}

@fragment
fn fragmentMain(input: VertexOutput) -> @location(0) vec4<f32> {
  let brightness = max(max(input.color.r, input.color.g), input.color.b);
  return vec4<f32>(input.color.rgb * input.color.a * (0.78 + brightness * 0.22), 1.0);
}
`
