# Comprehensive engineering audit

Status: **complete as a prioritized audit**, with limitations and deferred
checks recorded below. Pi/ARM64 and physical-device validation were explicitly
waived by the user; no such check is represented as passed.

## Review inventory

| Area | Current coverage and remaining work |
| --- | --- |
| Web authentication and sessions | Pairing, session expiry, and WebSocket access are reviewed; mutating HTTP API requests that include `Origin` must match the request origin. The deployed Nginx template forwards the host and HTTPS scheme needed by this check. Reverse-proxy integration and browser testing remain open. |
| Runtime, queue and settings | Command admission, shutdown, partial settings writes, power-limit validation, transient output errors, topology rollback and aggregate output cleanup reviewed and hardened; the broader worker operations and persistence paths remain. |
| Audio capture and DSP | Capture cleanup, callback-to-DSP buffering, callback timestamps, nonblocking real-time snapshots, beat-clock long-gap recovery, calibration clock handoff, selected native boundaries, RMS calculation, and stale-callback recovery reviewed. Sample-gap accounting now preserves native BPM timing after queue loss; focused release and sanitizer regressions pass. The current three-BPM offline music-sequencer sweep reproduces its documented results; phrase switching is unverified at 120/128 BPM and physical synchronization remains untested. Full DSP, routing, and timing-compensation coverage remain open. |
| Native render, GPIO and SPI | The renderer safety inventory covers all 51 unique registered entries and its 400 declaration/arithmetic anchors resolve to current C code. Targeted signed-overflow and float-to-int hazards were fixed; the focused renderer suite passes in release and ASan/UBSan/float-cast-overflow builds. All nine project-owned C translation units were source-scanned for ownership and buffer lifetimes; all nine renderer sequence conversions release their references on success and error paths, and no unmatched owned reference was found. Audio result/state-copy construction, GPIO allocation cleanup, module ownership, and driver-buffer release were also reviewed. All nine translation units compile with GCC `-fanalyzer` without first-party diagnostics. Per-expression arithmetic proof, allocator fault injection, and a complete all-entry sanitizer matrix were not performed; these limits are recorded in the native safety inventory. Hardware validation is waived by the user. |
| Animation, effects and playback | Catalog settings, native/Python parity suites, effect composition, and playback transition/sequence boundaries reviewed in part. Explicit non-finite transition and music-activity timestamps and non-finite brightness are rejected before state mutation. All 54 catalog entries have a render smoke sweep over five strip lengths. Individual visual behavior, every zone geometry, and long-running sequence timing still require systematic review. |
| Frontend | Shared WebSocket lifecycle, authentication routing, calibration, network setup, and major audio/device workflows reviewed; a source search found no direct HTML injection sinks or browser storage use. Runtime failures now close state, audio, and preview streams with 1011. The preview decoder rejects nonzero reserved flags and malformed/capped frames, while sequence ordering handles the documented uint32 wrap. Calibration preview cancellation on unmount waits for a live update before ending the session, and status failures show a retry screen. Manual mobile, screen-reader, and physical-device testing remain open. |
| Bluetooth, Spotify and networking | Network input limits, polling lifecycle, Spotify command delivery and malformed status handling reviewed in part; provider operations, PipeWire recovery and physical behavior remain open. |
| Deployment and security | Updater, TLS, Soloist privilege boundaries, Docker image and Compose hardening, and selected systemd sandbox settings reviewed in part. Soloist downloads require the official HTTPS host and archive handling uses bounded streaming and a member allowlist in the updater and image build; the upstream archive is still not authenticated with a pinned signature or digest. Installer migration, permissions, and hardware service recovery remain open. |
| CLI, simulator and shared support | Shared configuration construction and CLI/simulator parsing and lifecycle reviewed in part. Numeric arguments now reject NaN and infinities, and pixel counts must be positive before application startup. Simulator cleanup now closes microphone resources even after an output flush failure, and frame errors schedule shutdown instead of leaving Tk without a live tick. CLI device, keyboard, and real GUI shutdown behavior remain to verify. |
| CI, packaging and documentation | Optimized native validation and hosted/self-hosted trust boundaries reviewed; `none`, `audio`, and `all` profiles and Python 3.12–3.14 pass on x86-64. README, contributor, architecture, deployment, protocol, synchronization, ADR, and package guidance have been checked in part; benchmark CLI examples were compared with current help output. Remaining operational-claim review is open. ARM64 verification was explicitly waived by the user for this audit. |

## Findings

| Priority | Finding | Status |
| --- | --- | --- |
| High | Four-digit dashboard pairing accepted unlimited guesses. | A process-wide rolling limit of five failures per 60 seconds now blocks further attempts with HTTP 429 and `Retry-After`. It is shared across client addresses and protected by the session lock. Regression tests cover the limit and expiry. |
| Low | The web package README still described pairing attempts as unlimited after the rate limiter was implemented. | Updated it to document the five-failure window, shared per-process scope, restart behavior, and remaining weak-code exposure. |
| Medium | Cookie-authenticated mutating API routes accepted requests with a foreign `Origin`; `SameSite=Strict` alone does not reject same-site requests from a sibling origin. | Unsafe `/api/` requests now reject malformed or mismatched `Origin` values with 403. Requests without `Origin` remain supported for non-browser clients. Regression tests cover cross-origin pairing and logout plus same-origin success. |
| Medium | If the initial authentication-status request failed, the frontend's default unauthenticated state sent the user to the pairing screen, which presented a backend outage as a pairing problem. | The access hook now preserves a separate status-fetch error, and the app shows an unavailable screen with a retry action until access can be checked. An App integration test covers failure and retry. |
| Medium | Refreshing Wi-Fi adapters could select another interface after the selected adapter disappeared while retaining the old scan results and password draft. | If the selected adapter is no longer connectable, refresh now selects an available interface and clears the scanned network, SSID, password, and operation messages. Network controls are disabled while adapter status refreshes. A regression test covers adapter loss and reselection. |
| Medium | The preview v1 browser decoder accepted nonzero reserved flags and stream ordering rejected frames after the documented 32-bit sequence wrap; the backend encoder also allowed frames above the protocol's output and pixel limits. | The decoder now rejects nonzero flags and compares uint32 sequences modulo wraparound. The encoder rejects more than two outputs, more than 4096 pixels per output, and incomplete RGBA pixels. Backend tests cover encoder limits; frontend tests cover malformed headers, reserved flags, frame limits, and wraparound while retaining the shared golden packet. |
| Medium | Runtime errors in accepted state, audio, and preview WebSockets ended the handler without sending the documented 1011 close code. | All three handlers now attempt a 1011 close with a runtime-unavailable reason and safely tolerate an already-closed connection. Parameterized API tests cover each stream; all 43 API tests pass. |
| Low | Three native-render benchmark guides ran `setup.py` from the repository root even though in-place extension builds need the core package as their working directory. | The commands now use `uv run --directory packages/lumistripe-core`; a direct invocation confirmed that `uv` sets the expected package working directory before running the build. |
| High | The short pairing code remains vulnerable to sustained guessing, service restarts, and multiple independent worker processes; a shared limit can also deny legitimate pairing. | Residual risk: the limiter is in memory and per process. The documented deployment uses a single process and trusted network; do not expose it as a public authentication service. |
| High | The native renderer safety inventory explicitly leaves arithmetic, buffer, allocation-failure, and per-entry invalid-input proofs incomplete. | Review pending. Previous targeted sanitizer results and their limits are recorded in [native render hardening](../performance/native-render-hardening.md); they are not evidence of exhaustive safety. |
| High | Wave and Juggle native renderers added signed frame and pixel indices before conversion and narrowed large hue values to `int`, allowing undefined arithmetic on extreme frames. | Static hue now uses unsigned modular arithmetic; audio hue uses checked phase inputs and reduces finite hue values modulo 256 before conversion. Regression coverage exercises `Py_ssize_t`-maximum and large finite frames. The selected native renderer suite passes under ASan/UBSan and float-cast-overflow checks; remaining entry-specific risks stay listed in the inventory. |
| High | DropExplosion's native renderer added signed frame and pixel indices before taking small modulo values, and finite but out-of-range audio levels could reach floating-to-integer conversions. | Frame and index values now reduce separately before bounded arithmetic, preserving Python modulo behavior for both signed extremes. The native entry and wrapper require normalized audio values in `[0, 1]`. Extreme-frame parity and invalid-level regressions pass in release and ASan/UBSan builds. |
| High | ColorWipe's native renderer cast an expression containing unbounded finite audio values to `int`, which could overflow the representable range and invoke undefined behavior. | `local` and `boost` now require normalized `[0, 1]` values in both the C entry point and Python wrapper. Direct-native and wrapper regressions cover extreme finite and just-out-of-range inputs; the current renderer-kernel suite passes 145 tests in release and ASan/UBSan builds. |
| High | Fire's native renderer accepted unbounded finite controls before narrowing computed doubles to `float`. | Cooling, sparking, drive, bass, shimmer and accent now require normalized `[0, 1]` values in both C and Python, keeping the recurrence and float conversions bounded. Regressions exercise every control at negative and extreme finite values; the current renderer-kernel suite passes 145 tests in release and sanitizer builds. |
| Medium | Native Fire checked the `next_heat` output scratch buffer for finite contents before overwriting every element, so uninitialized or NaN-filled scratch could be read and rejected. | The C entry now validates only the output buffer's dtype, shape, alignment, contiguity and writability; it does not read the contents before writing. A NaN-filled scratch regression passes directly through the native entry and wrapper. |
| High | Confetti accepted unbounded finite audio values and bands before integer conversion, including `local * 18`; NeonConfetti also narrowed an unbounded finite fade multiplier to `float`. | Confetti control values and bands are now normalized in C and Python, and NeonConfetti's fade multiplier is bounded to `[0, 1]`. Direct-native and wrapper adversarial cases pass under ASan/UBSan with float-cast-overflow checks. |
| High | Comet accepted arbitrary finite shimmer and hue values; shimmer reached an integer lightness cast, while hue was cast to an integer palette index before modulo and could produce undefined conversion or an out-of-bounds lookup. | Shimmer is constrained to `[0, 1]`; neon and fire hue selection now applies `fmod` and corrects negative results before its bounded integer index. Direct C and Python-wrapper tests exercise extreme positive and negative hues and out-of-range shimmer under sanitizers. |
| High | FireworkBurst allowed glow above one to become an RGBA alpha byte; its Python wrapper also accepted out-of-range mid/high levels and audio bands that the native function rejected. | Glow, mid/high levels, and audio bands are now constrained to normalized ranges in the native entry and wrapper. Regressions cover extreme glow and out-of-range audio inputs at both boundaries. |
| High | GlowRush glow alpha and active ColorBurst strengths or PixelExplosion alpha could exceed the normalized range before conversion to an output byte. | Native functions validate active state before writing the destination, and Python wrappers apply the same `[0, 1]` checks. Direct-entry and wrapper regressions verify rejection without partial destination mutation. |
| Medium | Twinkle accepted unbounded finite bloom even though the wrapper and renderer parameters are expected to be normalized. | Native and Python entry points now require bloom in `[0, 1]`; invalid and extreme values are covered by regression tests. |
| Medium | Screen composition narrowed arbitrary finite doubles to `float`, including values outside the float range, which the C standard leaves undefined. | The C kernel saturates finite scalars at `FLT_MAX` before narrowing. Large positive and negative inputs retain the reference path's final channel clipping, covered by native/reference parity cases. See [WG14 C23 draft § 6.3.1.5](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3096.pdf). |
| Critical | The root updater copied service units, privileged helper code and sudoers templates from the service-user-writable checkout. A compromised service account could race the clean-tree check or alter Git state to install attacker-controlled content as root. Root also ran checkout-dependent status collection, allowing untrusted Git configuration to execute in the privileged process. | The installed updater now uses a root-owned deployment snapshot for privileged configuration, runs status collection and user configuration writes as the service user, and no longer imports user-writable TLS files or refreshes its own privileged helpers. Residual bootstrap risk: `install.sh` itself still reads and snapshots deployment files from the selected checkout as root, after recursively making that checkout service-user-writable. A compromised account can race migration and plant malicious privileged files; a clean-tree check and stopping the web/updater units do not close the race. Treat an in-place migration as unsafe if the service account may be compromised; restore from trusted administrator-controlled media and verify the installed root-owned snapshot. Later privileged deployment changes still require administrator review and installation. Isolated appliance validation and the wider deployment threat model remain open. |
| Medium | `LUMI_PROJECT_DIR` was used by the installer without canonicalization or checking that it named a LumiStripe checkout, before a recursive ownership change. | Shared validation now resolves symlinks and requires a non-root path with Git and LumiStripe project markers; installer and updater validate before privileged filesystem changes. Tests cover `/`, non-checkout directories, symlink resolution, and both call sites. The documented migration race for compromised service accounts remains. |
| Medium | Aggregate controller cleanup stopped at the first child exception, potentially leaving later GPIO/SPI outputs open. | Composite, multi-output and scaled controllers now attempt to close every child and re-raise the first error. A regression covers all three aggregate types with multiple close failures. |
| High | If a live or temporary topology change could not rebuild the previous outputs, the worker could keep running and report itself healthy with unusable hardware state. | Rollback failure now marks the runtime fatal, stops command processing, rejects new commands and invokes the configured recovery callback after cleanup. Standard and preview topology regressions cover failed recovery; cleanup failures during either path attempt every output operation and rebuild the previous topology when possible. |
| High | The root update service used the service user's home for curl configuration, and its lock lived in a shared lock directory. | The service uses root's home, root commands have a fixed system PATH, curl ignores user configuration, and the lock now resides in a root-owned runtime directory. Existing installations require the same administrator migration; review of the full deployment threat model remains open. |
| High | The updater downloaded Spotify Soloist as root, extracted archive paths in a root process, and installed the executable root-owned without checking archive structure. | Download and extraction now run as the non-root service account on the appliance; a shared Python helper enforces a 512 MiB compressed streaming cap even when `Content-Length` is absent, rejects non-HTTPS redirects and HTTP content encoding, validates each tar member as its header arrives, and accepts only one regular `soloist` executable plus the vendor's two known regular documentation members. It also caps decompressed archive bytes and read sizes before `tarfile` processes PAX metadata. An oversized or disallowed entry is rejected before the extractor advances to later members. The Docker build uses the same helper and its final image defaults to UID/GID 10001. Existing appliances must rerun the installer to replace the updater snapshot and service unit. Spotify's [official download page](https://developer.spotify.com/documentation/soloist/reference/downloads-and-updates) lists architecture-specific archives and build metadata; I found no published digest or signature there, so the binary's first download still relies on HTTPS and a compromised source can execute code as the service account or image user. |
| Medium | Root installer downloads used `curl --location` for mkcert and the uv/Bun installers without restricting redirect protocols or disabling curl configuration. An HTTPS-to-HTTP redirect could downgrade transport for privileged setup. | Both downloads now disable curl configuration and allow only HTTPS for the source and redirects. A regression checks both command paths; a direct curl check rejected an HTTP URL, `bash -n deploy/install.sh` passed, and all 31 updater/deployment tests passed. This constrains transport but does not authenticate publishers or replace the documented need for signed release metadata. See curl's [official `--proto-redir` documentation](https://curl.se/docs/manpage.html#--proto-redir). |
| High | During certificate creation, root previously read paths in a service-user-writable temporary directory; a substituted symlink could cause root to read a file unavailable to that account. | The installer reads generated files under the service account into root-owned staging, with size and time limits. Isolated tests exercise the user boundary and size checks; actual certificate generation and adversarial multi-user behavior remain unverified on an appliance. |
| Medium | The privileged network helper read unlimited standard input and could fail without a structured response on deeply nested JSON. | Requests are capped at 8192 bytes and excessive nesting returns an invalid-request response. Subprocess tests cover both cases; installed helpers require the administrator migration. |
| High | During rollback, Bash disables `errexit` within functions used as conditional commands; dependency synchronization or frontend build failures could be hidden by later successful commands. | Both functions now return explicitly on each failed step, including during rollback. Five isolated tests exercise each failure point. Other rollback operations and hardware recovery still require review. |
| High | Concurrent GPIO flushes could reallocate a buffer while another thread used it without the GIL; pin writes or reinitialization could also interfere with a flush. | Guarded operations while a flush is active, and initialized the descriptor in `tp_new` to avoid closing stdin on an uninitialized instance. Physical concurrency and pin restoration remain to be checked on a Pi. |
| Medium | SPI native encoding could overwrite aliased input while clearing its destination. | Overlap is rejected before either buffer is modified, with regression tests for both overlap directions. |
| Medium | SPI initialization could leave an opened device unclosed if opening, configuration or encoder allocation failed. | Initialization now closes the device on failure while preserving the original exception. Fake-device tests cover allocation, non-OS open failures and simultaneous configuration/close failures; physical SPI behavior remains untested. |
| Low | A shared-risk detail in the native renderer safety inventory still said 52 entries after its count note had been corrected to 51. | Programmatic comparison confirmed 51 unique registered methods, 51 inventory records, and 399 current declaration/arithmetic line references. The stale risk detail now uses the verified total. |
| High | Native audio accepted misaligned float buffers, narrowed unbounded batch lengths, and used 32-bit counters that could overflow during sustained operation. Invalid initialization values could also cause unsafe conversions or crashes. | Validated the native boundary, widened long-running counters, saturated the silence counter, normalized Python input alignment and contiguity, and added regression coverage. Exhaustive native safety review remains open. |
| High | The native audio sample-position counter could overflow when a valid discontinuity advanced it near `LLONG_MAX` and a later batch incremented it; finite maximum-range samples could also make cumulative sample telemetry infinite. | Feed batches now preflight the full counter increment before mutating state, and absolute-sample telemetry saturates at `FLT_MAX`. Regressions cover both limits; release and ASan/UBSan/float-cast-overflow runs pass. |
| Low | The SM16716 native encoder read a destination buffer's length after releasing its `Py_buffer` while formatting a short-buffer error. | The length is snapshotted before release, with a defensive negative-length check. Regression coverage asserts the reported required and available byte counts. |
| Medium | BPM estimation allocated temporary heap memory for a bounded inter-onset history and silently returned zero if allocation failed, which could publish an infinite BPM. | Median scratch now uses a fixed stack buffer; state-copy cleanup initializes its FFT pointer before allocation. A repeated 80 Hz pulse regression exercises BPM updates and verifies finite output. |
| High | The opt-in device-optimized native profile enabled fast-math and finite-math assumptions, causing non-finite validation to be compiled out. | Removed those flags while retaining explicit device-local optimization. Before the fix, two targeted audio tests failed under that profile; after rebuilding, both passed, as did three renderer non-finite cases. CI now rebuilds the optimized profile and runs those checks on hosted Python 3.12. ARM64 verification remains outstanding. |
| High | The native renderer's shared header was absent from the source distribution, so the `all` wheel build from that sdist failed. A pure-profile sdist also needed an explicit guarantee that all optional native sources are present. | The manifest includes all first-party native C sources and renderer headers. CI verifies these sources for every profile and builds an all-native wheel from a pure-profile sdist. The reproduced failing all-profile build succeeded after the fix. |
| Medium | Non-finite audio samples were cleaned for DSP but permanently poisoned the cumulative absolute-sample statistic. | The statistic now ignores non-finite samples, matching their treatment as silence by processing. |
| High | Audio stream startup failures could leak an opened stream, and a stream close failure could leave the DSP worker waiting indefinitely for its buffer to close. | Startup now closes an allocated stream; shutdown closes the buffer and joins the worker even if the backend raises. Fake-device regression tests cover both paths. Physical device-disconnect behavior remains untested. |
| High | A capture stream that stopped delivering callbacks after opening stayed selected indefinitely even when the route remained available. | `AudioInput` now exposes callback age without waiting for DSP work. The runtime reopens inputs with callbacks stale for more than 0.5 seconds, gives streams three seconds to produce their first callback, and continues route recovery if closing the stale stream raises. Regression tests cover a stalled microphone and the startup grace period. Device reconnection remains unverified on physical audio hardware. |
| Medium | Impossible PortAudio clock ordering could produce an audio timestamp later than the monotonic callback time, and direct DSP callers could inject non-finite capture timestamps that appeared fresh. | PortAudio clock mappings now fall back when the ADC block ends after the callback clock or arithmetic overflows; DSP uses monotonic time for non-finite supplied timestamps. Regression tests cover valid mapping, impossible ordering, and NaN/infinite DSP timestamps. Physical clock behavior remains unmeasured. |
| High | At the valid maximum fade setting, DiscoSparkle's non-audio decay factor exceeded one, so repeated native frames grew intensity beyond the wrapper's accepted range and raised `ValueError`. Long-running frame values could also overflow when converted or multiplied for hue animation. | Python and native paths now cap decay at one, native scalar inputs are range-checked, and frame hue arithmetic wraps safely for large frame counts. The rebuilt renderer passes Python/native parity at maximum fade and very large frame values, plus invalid-scalar tests; all 41 focused cases also passed with ASan/UBSan (leak detection disabled). |
| High | FireworkBurst's native entry parsed frame values as signed 32-bit integers, and range-valid audio phases could overflow signed arithmetic when forming particle offsets. Large LED indices could also overflow the band-index product. | The entry now accepts signed 64-bit frames, reduces frame, index, and phase values before small-integer arithmetic, and calculates the bounded band index without an integer product. Python uses exact integer floor division for the cycle. New regressions cover frames beyond 32-bit and the largest accepted phase; all three pass in release and under ASan/UBSan. |
| Medium | `BeatClockTracker` accepted non-finite clock and onset timestamps, which could propagate NaN beat times or make `state(inf)` raise `OverflowError`. Invalid BPM bounds and PLL gains were also accepted, and older onset timestamps could move the tracker anchor backward. | Update and state calls now reject non-finite times before mutation; construction validates positive ordered BPM bounds and finite gains in `[0, 1]`. Out-of-order onset timestamps are ignored without changing the last valid anchor. Initial BPM and reset now respect configured bounds. All 27 BeatClock tests pass on Python 3.12, 3.13, and 3.14. |
| High | Runtime command admission raced shutdown, potentially leaving a caller's future unresolved; cancellation during future completion could also raise in the worker. The queue and coalesced waiter lists had no limits. | Admission is checked under the command lock, shutdown rejects new and pending work, completion tolerates a concurrent cancellation, and queue entries and per-command waiters are bounded. Saturation returns `RuntimeUnavailableError` (HTTP 503). Tests cover shutdown, cancellation, queue saturation, coalescing and a full-queue shutdown. |
| High | An exception from a backend stop operation could abort runtime cleanup before other backends, the controller, or queued command futures were handled. | Cleanup now records the first failure and continues through the other cleanup steps. A simulated Bluetooth stop failure verifies Spotify cleanup, controller closure, pending-command rejection and the published error. |
| High | A hardware initialization stall could outlive the runtime startup timeout while the non-daemon worker remained alive and the watchdog ignored startup. | Timeout now requests worker shutdown and invokes the configured fatal-exit callback when initialization remains blocked, so the production service manager can restart it. A blocked factory test verifies late cleanup and the callback; real hardware behavior remains untested. |
| High | Partial dashboard settings writes ignored load warnings, so malformed, unreadable, partially invalid or newer-version files could be overwritten with defaults. | Partial updates now fail without modifying a file that could not be fully decoded; invalid version types and excessive JSON nesting are handled as invalid. Runtime and store tests verify the existing bytes and effective settings remain unchanged. An administrator must repair or restore such a file before saving again. |
| High | The CLI could leak the first GPIO output when opening a second failed, or skip encoder and audio cleanup when controller closure failed. | Secondary arguments are checked before opening GPIO, failed second-output setup closes the first, and both CLI run modes use guaranteed cleanup callbacks. Fake-device tests cover these paths; physical GPIO behavior remains untested. |
| Medium | Asynchronous WebSocket parsing could deliver older frames after newer ones, or a rejected parse from a disconnected socket could close its replacement. | Parse completion is scoped to its connection and ordered by message arrival; pending results are ignored once a connection starts closing. Hook tests cover reordered completion, reconnection, and parse failure. |
| Medium | Leaving the color-calibration page while a preview update was in flight could drop the cancellation request because the dashboard controller rejects concurrent commands; a pending debounced update could also be sent after unmount. | Unmount now clears unsent previews, waits for an in-flight update, then cancels the session. A component regression test verifies the order. |
| High | A timed-out Bluetooth poller or Spotify stop discarded its live thread handle, so a subsequent start could clear its stop event and create duplicate workers. An unexpected polling exception also terminated Bluetooth polling permanently. | Live handles are retained until their workers exit; Bluetooth polling logs and retries refresh failures. Simulated blocked-worker and recovery tests cover these paths. Hardware reconnection still requires validation. |
| High | Malformed JSON, invalid UTF-8 or non-finite Soloist values could interrupt status processing; a command that timed out while queued could still execute later, and pending commands were unbounded. Stale playback state also survived disconnect. | Malformed encodings and JSON are reported while the reader stays connected; status and command values are validated, positions and speeds are bounded, disconnected state is cleared, queued timeouts are cancelled before delivery, and the command queue is bounded. A send already in progress may still complete after a timeout and is reported as such. Regression coverage includes malformed bytes followed by a valid event. |
| High | The renderer watchdog treated synchronous worker commands as missing frames, potentially restarting the service during a slow Spotify operation. | Commands have a separate bounded eight-second watchdog deadline; frame heartbeat reporting remains tied to actual frames. Tests cover slow and stalled commands, including while blacked out. Commands can still pause rendering while they execute. |
| Medium | Audio dynamic-range calculation sorted the same RMS history twice and allocated two temporary arrays per frame. | Sort the bounded history once on the stack and calculate both percentiles from it. A rolling-percentile regression test and 480 deterministic feature snapshots confirm output parity for the exercised data. |
| Medium | Every runtime audio read waited up to one second for the bounded DSP queue to drain, adding backlog latency to the frame worker even though each state read already takes the DSP lock. | Frame, feature, health, and beat-event reads now return the latest completed state without waiting for queue idleness. Explicit state copies and reconfiguration retain their synchronization wait. A regression rejects queue waits from the real-time snapshot readers. |
| Medium | After more than two seconds without beat events, the beat clock retained its old phase and interval confidence history. The first recovered event refreshed freshness without re-anchoring, briefly presenting stale timing as confident. | The first event after a long gap now re-anchors phase, preserves the extrapolated beat index, and clears lock history until subsequent events rebuild confidence. Synthetic recovery tests cover phase, index continuity, and re-locking. |
| Medium | Audio-sync calibration stored DAC time and its monotonic-clock offset in separate callback-updated fields; `arm()` could read values from different output callbacks. | Calibration now publishes and reads one immutable timing-anchor tuple, keeping the tone and LED schedule on one callback's clock mapping without locking the real-time callback. A regression verifies `arm()` uses the coherent anchor. |
| High | Non-finite activity timing and cycling intervals could bypass comparisons; an infinite beat-density window could retain an unbounded history. `MusicActivityDetector.update` also accepted non-finite explicit timestamps, which could bypass beat-window pruning. | Configuration fields reject NaN and infinities, and activity updates reject non-finite timestamps before changing state. Playback regressions cover all three values for each affected field and update timestamp. |
| Low | The architecture guide omitted current runtime service boundaries; the deployment guide called its four-digit generated pairing value “secure”; the README confused the CLI with the GUI simulator and showed a development setup that differed from the contributor guide. | The architecture inventory now matches the current runtime and API modules; the pairing value is described as random; README commands identify the headless CLI and GUI simulator separately and use the documented native-audio profile. Bun guidance now follows `.bun-version`. |
| Medium | A selector history size of zero retained every distinct previous animation because a `-0` slice retained the entire list; invalid timing and score parameters were also accepted. | Zero history now stays empty, and selector timing and score settings reject non-finite or negative values. Focused selector, quantization and playback tests pass. |
| High | The exported quantized-switch coordinator could loop without advancing when given an extreme finite negative sync offset; its constructor also accepted invalid confidence and subdivision settings. | Timing inputs now require finite values and sync offsets within the runtime's ±0.5-second range. Constructor settings are validated consistently with sequence scheduling. Regressions cover NaN, infinities, extreme finite offsets, confidence boundaries, and boolean/invalid subdivisions. |
| Medium | `AnimationPlayer` accepted NaN or infinite explicit timestamps for transition start, progress, and rendering. A NaN start time could keep a transition active at zero progress forever. | Transition entry points now reject non-finite `now_s` before changing the selected animation, rendering a frame, or capturing transition data. Regression tests cover NaN and both infinities. |
| Medium | Core brightness setters passed NaN through `min`/`max` clamping, which could turn an invalid value into full brightness. | `BrightnessController` and `AnimationPlayer.set_brightness` now reject non-finite values before changing brightness. Tests cover NaN and both infinities; finite values retain the existing clamp behavior. |
| Medium | CLI and simulator positive/non-negative float argument parsers accepted NaN and infinity because comparisons with NaN did not reject it. Zero or negative pixel counts also passed parsing and failed later during buffer creation. | Both applications now reject non-finite numeric inputs and require a positive pixel count at argument parsing. Tests exercise NaN and both infinities across positive and non-negative options, plus zero and negative pixel counts. |
| Medium | Simulator shutdown skipped microphone cleanup and root-window destruction if an LED flush failed; an uncaught frame callback error could also leave Tk without a scheduled next tick. | Audio references are cleared before close, cleanup closes audio in a `finally` path, and the window is destroyed even when cleanup raises. Frame errors schedule shutdown. A regression verifies that audio closes after an output flush failure. |
| Medium | `AudioSnapshot` normalized accessors and `AudioReactive` frame adaptation used `min`/`max` directly, which mapped NaN to full scale and could make explicitly constructed invalid audio report maximum brightness, drive, accent, and activity. The audio capture and native DSP paths already sanitize non-finite samples. | Audio snapshot clamping and reactive frame adaptation now map non-finite levels to zero, consistent with the music-structure finite-value handling. Invalid BPM values also report zero. Tests cover NaN and both infinities across normalized snapshot and reactive values. |
| Low | The native audio constructor parsed `sample_rate` as `float` before checking its upper bound; values above `INT_MAX` could round to the same float and pass validation. | It now parses as `double` and validates the exact input before converting to the internal float. The regression confirms `INT_MAX + 1` is rejected. |
| Medium | Native BPM interval estimation counted processed FFT hops rather than elapsed captured samples, so dropped audio batches compressed the interval between later onsets. | A batch-aligned synthetic 80 Hz pulse train with nine 512-sample batches dropped between its first two onsets reported 234.91 BPM at the second event, versus 166.71 BPM for continuous input. The bounded queue now carries the exact dropped-sample count into DSP, and native BPM intervals use elapsed sample positions. The regression passes with 166.71 BPM in both streams and zero beat-timestamp deviation. |
| High | A single output `OSError` escaped the frame loop, shut down the runtime, and requested a service restart; one disconnected output could interrupt the full dashboard. | `OutputGateController` now retries I/O failures with exponential backoff capped at five seconds, keeps other output writes running, and exposes the latest error in the existing output state. A successful write clears the error. Non-I/O runtime errors remain fatal. Runtime tests cover backoff, recovery, reported status, and the fatal programming-error path. |
| Medium | The software-timed libgpiod fallback cleared dirty flags before toggling GPIO pins, so a mid-write I/O error could make retries skip the frame. | Dirty flags are cleared only after the full frame write succeeds. A fake line writer that fails its first pin operation verifies the failed frame remains dirty and the following flush retransmits it. |
| Medium | Non-finite LED calibration, frame channels, and budgets could produce NaN power estimates or invalid brightness scales; settings and API models also accepted infinite watt values. | Core estimates and settings reject NaN and infinities, power sums reject overflow, and Pydantic request/response models disallow non-finite watt and scale fields. The runtime's `uint8` path now contracts RGB with alpha directly, avoiding normalized-frame and per-pixel duty intermediates. Regression tests cover the invalid numeric boundaries and reference duty calculation. |
| Medium | The Codecov token was available in the Python CI job environment to lint and test subprocesses. | The token is now scoped to the two upload steps, and uploads are skipped for pull requests. Hosted PR checks still run without needing the upload credential. |
| Medium | Spotify and update-check service units had no systemd sandbox restrictions. | The unprivileged Spotify and update-check services now use `NoNewPrivileges`, private temporary storage, and read-only system paths. The web unit also gets a private `/tmp` and read-only system paths; it omits `NoNewPrivileges` because its authenticated network workflow relies on a sudoers-limited root helper. Rendered-unit verification passed; appliance behavior remains untested. |
| Medium | The runtime scheduled each next frame one requested interval after the previous frame finished, although deadline tracking measured the interval from the scheduled due time. Render work therefore reduced the achieved cadence even when no deadline miss was recorded. | The worker now schedules from the due time and avoids catch-up bursts after overruns. A regression covers on-time cadence, overruns, and the minimum interval. A matched synthetic host run improved from 47.4 to 50.0 FPS at a requested 20 ms interval; actual p50 frame spacing moved from 20.832 to 19.9999 ms. Raspberry Pi performance remains unmeasured. |

## Baseline

On Linux x86-64, Ryzen 9 9900X, Python 3.12.15, the simulated runtime
benchmark with 300 LEDs, mirrored layout, one zone, dynamic mode, native
rendering, and power limiting off ran for 5 seconds after 1 second warmup.
It recorded 292 frames (58.40 fps), one reported deadline miss, and `_step`
p50/p95/p99 of 0.826/3.106/3.862 ms. This short synthetic run uses virtual
buffers and demo audio; it is neither a Raspberry Pi measurement nor a
performance improvement claim. Command:

```sh
uv run python scripts/benchmark_runtime_pipeline.py --led-counts 300 --layouts mirrored --zone-counts 1 --modes dynamic --power-limits off --warmup 1 --duration 5
```

The synthetic `benchmark_audio_dsp.py --iterations 1024` x86-64 run before
and after the audio boundary changes gave 512-sample feed p50/p95/p99 of
0.0249/0.0268/0.0415 ms and 0.0251/0.0262/0.0301 ms respectively. The
single short runs are noisy and do not establish a speedup or Pi performance.
For 8192-sample calls p50 was 0.2947 ms before and 0.2968 ms after; p95 was
0.3157 and 0.3789 ms. Physical audio latency and allocations remain unmeasured.

For the subsequent RMS-sort optimization, three alternating runs of
`benchmark_audio_dsp.py --iterations 8192` on the same x86-64 machine gave
the following medians of each run's reported percentiles (milliseconds):

| Samples per call | Before p50 / p95 / p99 | After p50 / p95 / p99 |
| --- | --- | --- |
| 512 | 0.025127 / 0.026930 / 0.037701 | 0.023704 / 0.025287 / 0.033703 |
| 2048 | 0.078747 / 0.082935 / 0.097542 | 0.069801 / 0.072546 / 0.075792 |
| 8192 | 0.288028 / 0.295502 / 0.301033 | 0.255929 / 0.261129 / 0.270205 |

These synthetic results are not physical Pi measurements. The implementation
removes two temporary allocations per evaluated frame by inspection; allocation
counts were not independently measured.

For the power-estimation fast path, a deterministic 300-pixel `uint8` frame
was measured on the same x86-64 host with 1,000 warmup calls and 10,000 timed
calls per run. The medians of three runs' percentile measurements were:

| Estimator p50 / p95 / p99 | Before | After |
| --- | --- | --- |
| Time (microseconds) | 8.656 / 8.786 / 10.329 | 2.836 / 2.875 / 4.418 |

This reduces the isolated estimator's median p50 and p95 by about 67% and p99
by about 57%. The fast path uses NumPy's direct contraction and has no
normalized-frame or per-pixel duty intermediates by inspection; allocation
traffic was not separately profiled. Two five-second static runtime runs with
300 LEDs recorded 60.8 FPS (limiting off) and 61.0 FPS (limiting on), with no
missed deadlines. Output-stage p50/p95/p99 was 0.093/0.178/0.212 ms off and
0.100/0.192/0.238 ms on. These short simulated runs show no meaningful
end-to-end rate change and are not Pi measurements.

The runtime frame-scheduling correction was checked with matched 10-second
synthetic dynamic runs on the same host, using 300 virtual LEDs, mirrored
layout, one zone, native rendering, and power limiting off. A control run
monkeypatched the old completion-relative scheduling policy; the comparison
run used the corrected due-time-relative policy. The old policy recorded 474
frames (47.40 FPS) with actual frame-start intervals p50/p95/p99 of
20.832/23.328/24.001 ms. The corrected policy recorded 500 frames (50.00 FPS)
with intervals of 20.000/20.012/20.029 ms. Both runs reported zero missed
deadlines, illustrating that the prior counter did not capture this cadence
drift. This is a single host comparison on synthetic audio and virtual output,
not a Pi measurement. The benchmark now records frame-start interval
percentiles in `scripts/benchmark_runtime_pipeline.py`.

An initial full Python run before the native edits passed 6,482 tests with
168 skipped. A subsequent run with the main native changes passed 6,487 tests
with 168 skipped; it preceded the final uninitialized-copy guard and the new
updater tests, which passed separately. The all-native x86-64 build succeeded;
110 focused audio, stripe, and updater tests passed after those edits. GCC
`-fanalyzer` also built the audio, GPIO and SPI extensions without warnings in
project-owned C sources; compiler warnings remain in NumPy headers and vendored
KISS FFT. Physical GPIO/SPI operation was not tested.
The same 105 focused tests passed against an isolated x86-64 build with
AddressSanitizer and UndefinedBehaviorSanitizer enabled (`detect_leaks=0`,
UBSan halt on error). This does not exercise actual GPIO hardware, allocation
failure paths, or every native entry point, and it does not check leaks.
After the updater trust-boundary and audio cleanup changes, 75 focused tests
passed and Ruff, mypy, shell syntax, and `git diff --check` passed. Updater
tests use isolated shell functions and temporary paths; the installer,
systemd, Nginx, and actual migration have not been run on an appliance.
The subsequent full Python run passed 6,499 tests with 168 skipped; it
preceded the certificate-import and network-input tests. The frontend suite
passed 162 tests and the production build succeeded. The 26 focused network,
updater and certificate tests passed after these changes.
After the RMS-sort change, 68 audio and music-structure tests passed both
normally and with ASan/UBSan (leak detection disabled). The first sanitizer
run had three cleanup-test failures because its temporary package contained an
older `capture.py`; after synchronizing that file with the checkout, all 68
passed. This is not a complete native safety proof.
The new optimized-profile CI commands were also run in an isolated x86-64
environment: all five selected non-finite validation cases passed. The
normal all-native extensions were rebuilt afterward.
The `none`, `audio`, and `all` core wheels were checked for their expected
native extensions; both pure and all-profile sdists contained the tested
renderer, GPIO, SPI and KISS FFT sources. An all-native wheel also built from
the pure-profile sdist. Against the extracted pure wheel, render and sequence
tests passed 45 cases with 31 native-only skips. A later full Python run passed
6,517 tests with 168 skips before the subsequent CLI and startup fixes.
After the runtime, settings and CLI changes, their combined CLI, runtime,
settings and API test selection passed 212 tests; the complete Python suite
was restarted for the updated worktree.
That full run passed 6,522 tests with 168 skipped before the subsequent
Bluetooth, Spotify and watchdog changes. A 139-test integration selection and
21 focused Spotify/poller tests passed after those changes; tests use mocks,
not a physical Spotify, BlueZ or PipeWire connection.
The next full Python run passed 6,539 tests with 168 skipped before the latest
SPI, playback, and selector validation edits; focused suites passed separately.
The next full run on the updated worktree passed 6,579 tests with 168 skipped
(87% aggregate coverage). It preceded the final animation-transition and CLI
argument validation. After the transition edit, all 60 layer tests passed;
after parser validation, the CLI suite passed 87 tests and simulator suite
passed 53 tests. Repository Ruff and the full configured mypy target passed,
and `git diff --check` was clean. The audio suite passed 73 tests with the
animation sanity checks after the finite-value snapshot/reactive fix. After the
Soloist privilege-boundary change, 18 isolated deployment tests passed,
including regular-file, path traversal, symlink, extra-member, service-user
installation, and service restart cases. Bash syntax, repository Ruff, and
`git diff --check` passed. The deployment selection emitted expected coverage
warnings because it imports no configured application package. After the
official-origin redirect restriction, the Soloist deployment/updater suite
passed 31 tests on Python 3.12.15, 3.13.18, and 3.14.8. Coverage includes
bounded streaming downloads, official-origin-only HTTPS redirects,
archive-member restrictions, and acceptance of the vendor's two known
documentation files. Repository Ruff passed. Path tests reject `/` and
non-checkout directories, resolve checkout symlinks, and verify both privileged
scripts validate before mutation. Regression tests confirm oversized members
are rejected before the next archive header is read and oversized PAX metadata
is rejected under the decompressed-byte cap. The local x86-64 Docker
image built with the shared downloader, and image inspection confirmed its
default user is `10001:10001`; the image was built but not started. `docker
compose config --quiet` and rendered systemd template verification passed.
Docker Compose service behavior and appliance migration remain untested. The
An earlier full Python matrix, before the latest renderer and Spotify boundary
changes, passed on Python 3.12.15 in 202.08 seconds, Python 3.13.18 in 199.68
seconds, and Python 3.14.8 in 74.19 seconds. Each run passed 6,735 tests with
168 skipped at 87% aggregate coverage. The renderer-focused
kernel/catalog/SpectrumFlash suites passed 2,722 tests with 168 expected
fallback skips under ASan/UBSan and float-cast-overflow checks. Leak detection
was disabled, and per-entry arithmetic and allocation-failure proofs remain
incomplete.
The DropExplosion native entry separately passed 18 release tests and the same
18 cases under ASan/UBSan with float-cast-overflow checks, including signed
`Py_ssize_t` extremes in static and audio modes. The sanitizer build emitted
only the existing NumPy C-API pedantic warnings; leak detection was disabled.
After the ColorWipe, Fire, Confetti, NeonConfetti, compositor, Comet,
FireworkBurst, GlowRush, ColorBurst, PixelExplosion, Twinkle and Fire scratch
buffer boundary changes, the complete `test_render_kernels.py` suite passed
145 tests in both release and ASan/UBSan builds with float-cast-overflow
checks. Leak detection
was disabled; native compilation emitted the existing NumPy C-API pedantic
warnings, and the focused pytest run emitted coverage warnings for unrelated
packages it did not import.
After the topology cleanup and fatal rollback changes, the focused controller and
runtime suites passed 100 tests; the newly added aggregate-close and topology
rollback regressions passed five cases. Repository Ruff and the configured full
mypy target pass on this revision. These focused checks supplement, but do not
replace, the three full Python-version runs recorded above.
The audio, beat-clock, runtime, and sync-calibration suites passed 174 tests;
the playback, layer, sequence, and quantized-switching suites passed another
132 tests after the beat recovery, timing-anchor, and real-time snapshot
changes. Ruff and the configured full mypy target pass. Raspberry Pi audio
timing and route behavior still need hardware validation.
The latest frontend run passed
165 tests in 26 files; OpenAPI regeneration was reproducible, frozen Bun
installation made no lockfile changes, lint passed with non-fatal React
warnings, and the production build succeeded. Ruff, the configured full mypy
target, and `git diff --check` passed. All three latest Python-version runs
used x86-64; physical ARM64 checks were not performed.

The music-activity timestamp validation passed the playback test module
(46 tests). The brightness boundary regressions passed with controller and
playback tests (85 tests), and quantized-switch, sequence, and playback tests
passed 92 cases after scheduler input validation. Ruff passed on the changed
files. Graphify was refreshed after source edits; it reports 6,061 nodes.
Extraction still reports partial parsing of `_audio.c` and vendored KissFFT
headers, plus skips for unsupported file types.

Before the latest native audio median change, Python 3.12, 3.13 and 3.14 each
passed the complete suite: **6,853 passed and 168 expected skips per version**.
The simulator shutdown regression also passed its focused module on all three
versions (54 tests per run). The first Python 3.14 run
exposed the Fire scratch-buffer read; after correcting the native validation,
all three version runs passed. An earlier Python 3.13 attempt loaded a stale
ignored extension and reported 66 failures; rebuilding the extension from the
current sources resolved those test-environment failures. Renderer kernels
passed 145 tests in both release and ASan/UBSan/float-cast-overflow builds,
and Spotify/Bluetooth poller tests passed 21 cases. Repository Ruff and the
configured mypy target passed after the simulator change; `git diff --check`
and Graphify refresh also passed. The reviewed deployment shell scripts passed
`bash -n`. After the native audio change, the audio module passed 75 focused
tests on Python 3.12. GCC `-fanalyzer` builds of all nine project-owned C units
succeeded without project-source diagnostics; warnings came from NumPy headers
and vendored KISS FFT.

After the native audio median, sample-rate boundary, and runtime cadence
changes, the complete suite passed on Python 3.12.15, 3.13.18, and 3.14.8:
**6,855 passed and 168 skipped on each version**, with 87% aggregate coverage.
Python 3.12 completed in 202.27 seconds, Python 3.13 in 200.82 seconds, and
Python 3.14 in 74.06 seconds. Repository Ruff and the configured mypy target
also pass on the updated tree; the runtime benchmark instrumentation tests
pass four cases.

After adding dropped-sample timing metadata to the bounded audio queue and
changing native BPM intervals to use elapsed sample positions, the focused
audio and boundary suites passed 84 tests in release and ASan/UBSan/float-cast-
overflow builds. GCC `-fanalyzer` reported no first-party diagnostics; full
Ruff and the configured mypy target pass. The complete suite then passed on
Python 3.12.15, 3.13.18, and 3.14.8: **6,859 passed and 168 skipped per
version**, with 87% aggregate coverage. The runs took 202.03, 200.42, and
75.32 seconds respectively. This matrix includes the installer and dropped-
sample timing changes.

After the root installer's HTTPS-only download and redirect restriction,
`test_update_script.py` passed all 31 tests, `bash -n deploy/install.sh`
passed, and curl rejected a direct HTTP URL with the configured protocol
policy. These focused checks cover the final shell and deployment-test edits;
the earlier 6,855-test matrix preceded this shell-only change; the later
6,859-test matrix above includes it.

The current renderer arithmetic batch passed `test_render_kernels.py` with
**160 tests** in both release and ASan/UBSan/float-cast-overflow builds. New
regressions cover extreme frames and seeds, finite phases near conversion
limits, wrapped segment indices, and invalid normalized audio controls. The
machine-readable renderer inventory now has 400 declaration/arithmetic
anchors that resolve against the current C files; this is an index refresh,
not a completed proof of every listed expression. Leak detection was disabled
for the sanitizer run.

The latest release run of `test_audio.py`, `test_stripe.py`, and
`test_render_kernels.py` passed **288 tests**. ASan/UBSan/float-cast-overflow
checks passed all **79 audio tests** and **8 SM16716 encoder cases**. GCC
`-fanalyzer` builds of the changed audio, GPIO, and SPI extensions produced no
project-owned diagnostics; warnings came from NumPy headers and vendored
KissFFT. Raspberry Pi GPIO/SPI behavior remains untested.
The same 288 audio, stripe, and renderer tests also passed with Python's debug
allocator enabled; this does not replace native leak detection or allocation-
failure injection.

The latest full Python 3.12 suite after the preview and WebSocket fixes passed
**6,883 tests with 168 skipped** (87% aggregate coverage). An earlier run
exposed an over-tightened native LightningStrike `all_flash` maximum: the
animation formula can produce values up to 1.5 from a normalized flash alpha.
The Python wrapper and C boundary now accept the full derived range and reject
values above it, with an exact-bound regression. The LightningStrike parity
selection passed 89 tests, and the renderer kernel suite passed 160 tests after
the correction. The preview protocol suite passed **5 backend tests**; the complete
frontend suite passed **167 tests**, and its production build succeeded.
Repository Ruff and the configured mypy target passed, and `git diff --check`
was clean. Frontend lint exits successfully with existing Oxlint warnings.

After the WebSocket close-path change, the full API test module passed **43
tests** and the configured mypy target again passed all 173 source files.
The current all-native core build produced a wheel with all four expected
extensions and a source distribution containing the audio, renderer, GPIO,
and KISS FFT sources. A repository-wide Markdown link scan found no broken
local paths; documented benchmark command flags were checked against each
script's current `--help` output. The workspace build produced all five wheels
and sdists; the default core wheel is pure Python and its sdist contains the
native renderer, GPIO, and KISS FFT sources.
The current structured-track sweep at 120, 128, and 150 BPM reproduced the
documented structure, beat-clock, switching, and sequence results. Phrase
switching is directly covered by the playback coordinator's beat/bar/phrase
boundary tests; the focused module passed **28 tests**. In the end-to-end track
run, phrase switching had no scheduled candidate at 120/128 BPM and remains
unverified there; it passed at 150 BPM. Physical synchronization remains
unverified.
`bash -n` passed for every deployment shell script in `deploy/`.

Architecture and deployment guides were cross-checked against the current
module and installer layout. The preview and WebSocket contracts, audio-sync
and sequencer procedures, build-profile ADRs, package guidance, security
claims, local Markdown links, and benchmark command examples were checked.
The audit is complete at the prioritized scope requested by the user; the
following residual risks and lower-priority checks remain explicit.

## Deferred follow-up and known limitations

- The root installer bootstrap risk is not safely closed by this checkout:
  privileged deployment files are imported from the checkout during
  installation. The deployment guide now requires a trusted administrator-
  controlled checkout; if service-account compromise is suspected, restore
  from trusted media before reinstalling. This risk is documented as
  unresolved.
- Spotify does not publish a checksum or signature with the configured
  Soloist archive. Downloading is limited to the official HTTPS origin,
  bounded and parsed with an allowlist, and runs as the service user; a
  compromised official source could still execute code as that user.
- The native safety inventory records that formal per-expression arithmetic
  proof, Python allocator fault injection, and a complete all-entry sanitizer
  matrix were not performed. All nine project-owned C translation units were
  source-scanned for reference and buffer lifetimes; GCC `-fanalyzer` found
  no first-party diagnostics, and focused ASan/UBSan/float-cast-overflow tests
  passed.
- The structured-track integration sweep had no scheduled phrase switch at
  120 or 128 BPM; phrase-boundary behavior is covered by the passing direct
  coordinator tests and the integration case passed at 150 BPM.
- Installer migration and physical service recovery were not run on an
  appliance. The updater and deployment helpers have isolated tests, shell
  syntax and rendered-unit checks; hardware behavior was waived by the user.
- Manual mobile and screen-reader checks were not performed. Raspberry
  Pi/ARM64 builds, benchmarks, and physical audio, SPI, GPIO, Bluetooth,
  PipeWire, and service-recovery checks were explicitly waived and remain
  untested, not passed.
