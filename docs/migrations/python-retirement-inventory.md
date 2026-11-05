# Python retirement inventory

## Current status

Goal 12 completed the production retirement of the Python application stack.
LumiStripe's supported web app, CLI, playback engine, simulator, and deployment
helpers are Rust-only. The React dashboard remains the frontend.

| Retired path | Current status |
| --- | --- |
| FastAPI web backend, runtime worker, routes, Pydantic contracts, and Python OpenAPI exporter | Removed. Rust Axum models and the Rust OpenAPI exporter are authoritative. |
| Python CLI and Python app-support package | Removed. The Rust CLI and engine expose the supported commands and policy. |
| Python core library and audio/GPIO/SPI/render C extensions | Removed with their package metadata, tests, setup code, and KISS FFT submodule. |
| Python production install/update/launcher profiles | Removed. Install, update, and rollback build commit-matched Rust releases. |
| Python/Rust process differential suites and obsolete Python benchmark/golden generators | Removed after retaining Rust-side parity tests and checked-in fixtures. |
| Root Python application workspace, `uv.lock`, and Python distribution CI | Removed. |

## Rust evidence retained

Rust tests and fixtures continue to cover the compatibility behavior that
matters at current boundaries:

- `rust/apps/lumistripe-web/src/request_validation.rs` checks legacy request
  coercions and validation errors.
- Rust web application tests cover OpenAPI export, authentication, validation,
  settings persistence and migrations, simulator startup, WebSockets, and
  application behavior without a Python executable.
- `rust/apps/lumistripe-web/src/settings.rs` preserves existing and
  forward-compatible settings fields.
- Preview protocol v1 retains its shared golden packet and frontend fixture.
- Engine tests retain checked-in animation, effect, layout, transition, power,
  and audio DSP reference data.
- CLI and deployment-helper process tests exercise their native executables
  with a PATH that has no Python interpreter.

Keep these fixtures as Rust regression inputs. They do not require a Python
runtime or the old implementations.

## Remaining Python development tools

Python is not a LumiStripe application or deployment dependency. Python and
pytest remain optional for the deployment regression harness in
`deploy/tests/`; CI installs pytest in an isolated `uv run --no-project`
environment. `scripts/generate_audio_sync_track.py` is a separate optional
audio-fixture utility and requires NumPy. Neither tool is installed or called
by production services.

## Deployment migration behavior

The installer, updater, and web launcher reject
`LUMI_WEB_BACKEND=python` with an actionable message. The installer and
updater check the selection before changing the environment file or
application data. They do not rewrite a saved Python value. The administrator
must change it to `LUMI_WEB_BACKEND=rust` manually and rerun the operation.
Rust settings migrations preserve existing settings.

The deploy test suite checks this refusal, checks that the configuration file
remains byte-for-byte unchanged, verifies the launcher uses the commit-matched
release, and exercises updater rollback and health checks against a restored
Rust release.

## Validation boundary

The software validation for this retirement is requested separately from
hardware commissioning. No Raspberry Pi or physical hardware checks were run
for Goal 12. In particular, these current-worktree changes were not verified
against:

- Physical SPI/GPIO LED output, waveform, blackout, or long-duration recovery.
- Real microphone capture and calibration.
- Bluetooth pairing/playback, PipeWire routing/recovery, or live Spotify.
- Raspberry Pi 4 hardware.

Older dated results in the migration and performance documents remain
historical evidence for the exact revisions and environments recorded there;
they do not validate this retirement change. See the
[architecture guide](../architecture.md), [deployment guide](../../deploy/README.md),
and [simulator guide](../simulator.md) for current instructions.
