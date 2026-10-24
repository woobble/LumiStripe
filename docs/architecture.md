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

The web backend owns HTTP contracts, authentication, Bluetooth and Spotify
providers, runtime lifecycle, and state projection. The frontend consumes the
backend contract and never reaches into Python implementation details.

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

- `api/routes/`: system, control, stripes, sequencer, audio/Bluetooth, startup,
  auth, and WebSocket routes
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

- `lumistripe.audio.types`, `.config`, `.capture`, `.dsp`, `.devices`,
  `.calibration`, `.structure`, and `.timeline`; capture callbacks enqueue
  bounded sample batches, the DSP worker owns native processing state, the
  structure tracker classifies timestamped features for Dynamic playback, and
  the timeline maps the beat clock onto an estimated 4/4 bar and phrase grid
- `lumistripe.playback.config`, `.activity`, `.state`, `.renderer`, `.engine`,
  and `.sequence`; Dynamic playback passes the current structure state to the
  effect scheduler, while `PlaybackDecision` and `RenderPlan` remain pure
  policy outputs
- `lumistripe.animation.catalog`; `AnimationDefinition` is the single
  registration record used to build the party player
- `lumistripe.effects.definitions`, `.scheduler`, and `.renderer`
- `lumistripe.output`

The core package is pure Python by default. Native audio, GPIO, and SPI
extensions are selected explicitly with `LUMISTRIPE_BUILD_EXTENSIONS`, and
optimized `-march=native` builds are reserved for device-local use. See
`docs/decisions/0003-native-extensions.md` for the release and CI policy.

These modules are the intended seams for future implementation moves.

## Dynamic animation switch quantization

Dynamic mode can defer automatic base-animation changes to the next confident
beat, estimated bar, or estimated phrase boundary. `off` remains the default;
manual animation selection and the other playback modes are unaffected. Bar
alignment starts at the first observed beat and is an estimate, not detected
downbeat information. Phrase length is configurable to 4, 8, or 16 bars.

The authenticated `GET` and `PUT /api/sequencer/settings` endpoints expose the
shared sequencer settings. Updates enter the runtime worker command queue,
apply to all outputs without restarting playback, discard pending switches
from the previous grid, and persist in dashboard settings version 8. Version 7
settings retain their sequencer choices and migrate with an empty sequence
library; older settings receive the established defaults. Saved audio
synchronization offsets remain intact.

Dashboard state reports per-output estimated beat, bar, and phrase positions,
clock confidence, pending animation, remaining time to its switch, and whether
the switch is waiting for confidence. These diagnostics are read-only and do
not imply that the estimated grid has detected musical downbeats.

## Core animation sequences

`PlaybackConfig.animation_sequence` optionally enables a core-only Dynamic
sequence. An `AnimationSequence` contains at most 32 ordered
`AnimationSequenceStep` values, each using an eligible automatic Dynamic base
animation for 1, 2, 4, or 8 estimated bars. The sequence starts on the next
confident estimated bar boundary, applies the configured audio sync offset,
and can repeat or return to the automatic Dynamic selector when it finishes.

`AnimationSequenceCoordinator` freezes the current step while beat-clock
confidence is low. On relock or after a stalled render skips bar boundaries,
it reanchors the remaining duration to future boundaries without catching up
or replaying missed steps. Its immutable `SequencePlaybackState` exposes the
active step, remaining bars, waiting state, and next scheduled transition.
Sequence playback remains disabled by default and uses the same animation
player crossfades and effect rendering path as ordinary Dynamic playback.

The authenticated sequence API uses `GET` and `POST /api/sequencer/sequences`,
`PUT` and `DELETE /api/sequencer/sequences/{id}`, and
`PUT /api/sequencer/active`. It stores up to 20 definitions and the optional
selected ID, but no playback clock state. Mutations go through the runtime
worker and save atomically before applying the selected sequence to the shared
and independent playback engines. Selecting `null` returns Dynamic mode to its
automatic selector. A completed non-repeating sequence keeps its selection for
the next active music session.

Dashboard state includes the saved sequence IDs and names, selected ID, and
per-output progress: whether a sequence step is active, its zero-based step
index and animation, remaining bars, clock-wait state, and a relative time to
the next transition. Estimated bar alignment remains an estimate.
