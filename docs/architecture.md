# LumiStripe architecture

LumiStripe is a Python workspace with hardware-focused libraries in
`packages/` and user-facing applications in `apps/`.

## Dependency direction

```text
lumistripe-core
        |
lumistripe-app-support
        |
  +-----+------------------+
  |                        |
 CLI                    Simulator
  |
 Web backend ---- Frontend
```

`lumistripe-core` owns pixel buffers, controllers, animations, effects,
playback, audio processing, and hardware adapters. It must not depend on
FastAPI, browser code, or application-specific configuration.

`lumistripe-app-support` contains UI-agnostic configuration builders and
policies shared by the CLI, simulator, and web runtime. It may depend on core,
but not on any application.

The web backend owns HTTP contracts, authentication, Bluetooth providers,
runtime lifecycle, and state projection. The frontend consumes the backend
contract and never reaches into Python implementation details.

## Runtime ownership

`LumiStripeRuntime` is the web façade. Commands enter through its queue and
are executed by one worker, which owns mutable playback, controller, audio,
and hardware state. API handlers may read immutable snapshots but must not
mutate hardware directly.

The runtime package is organized around this ownership boundary:

- `runtime/commands.py`: typed command messages
- `runtime/state.py`: settings, snapshots, sessions, and factory protocols
- `runtime/audio.py`: audio input construction
- `runtime/topology.py`: controller and persisted topology construction
- `runtime/outputs.py`: output gates and frame ownership
- `runtime/__init__.py`: façade, worker loop, and orchestration

The API package follows the same feature boundary:

- `api/routes/`: system, control, stripes, audio/Bluetooth, startup, auth, and
  WebSocket routes
- `api/dependencies.py`: runtime lookup and command error mapping
- `api/protocols/preview.py`: binary preview framing
- `contracts/`: feature-oriented HTTP model namespaces

Further service extraction should preserve the single-worker invariant.

### Renderer timing diagnostics

The worker records the duration of each completed render step and compares it
with the effective animation interval returned by playback, clamped to the
16 ms minimum frame interval. A frame deadline miss is therefore a rendering
step that completes after its scheduled presentation budget; it is not the
same as a stalled worker.

`missed_frame_count` is cumulative for the current runtime session and is
useful for historical telemetry. The Diagnostics page uses a separate rolling
10-second window, requiring at least five misses and a 10% miss rate before it
reports an active warning. This prevents a transient startup spike from
remaining visible indefinitely while still surfacing sustained CPU, animation,
or hardware-output pressure.

## Core boundaries

The core package keeps compatibility exports in `lumistripe.__init__`, while
new integrations should use narrower modules:

- `lumistripe.audio.types`, `.config`, `.capture`, `.dsp`, `.devices`, and
  `.calibration`; capture callbacks enqueue bounded sample batches and the DSP
  worker owns native processing state
- `lumistripe.playback.config`, `.activity`, `.state`, `.renderer`, and
  `.engine`; `PlaybackDecision` and `RenderPlan` are pure policy outputs
- `lumistripe.animation.catalog`; `AnimationDefinition` is the single
  registration record used to build the party player
- `lumistripe.effects.definitions`, `.scheduler`, and `.renderer`
- `lumistripe.output`

The core package is pure Python by default. Native audio, GPIO, and SPI
extensions are selected explicitly with `LUMISTRIPE_BUILD_EXTENSIONS`, and
optimized `-march=native` builds are reserved for device-local use. See
`docs/decisions/0003-native-extensions.md` for the release and CI policy.

These modules are the intended seams for future implementation moves.
