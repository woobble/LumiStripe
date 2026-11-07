# Release readiness

This document separates checks that CI executes from acceptance work that must
be performed on an installed system. Automated checks establish software-level
release confidence; they do not certify Raspberry Pi hardware behavior.

## Coverage audit

The gap list below records the baseline audit completed before implementation.
Since that audit, the release path now exercises the exact packaged ARM64
directory before publication in Debian Trixie and Ubuntu 26.04 ARM64 runtime
containers. The deployment suite also runs the production administrative
entrypoint in a disposable Debian Trixie container for fresh and repeated
source installs, a fresh binary install from a previously verified offline
cache fixture, and unchanged and successful source updates. Through the
installed admin command it also performs an offline binary update, confirms
rollback after an injected web restart failure, rejects a missing cache, a
modified verified archive, and a wrong-architecture manifest without building
from source, retries successfully, and checks that a repeated update does not
restart the web service. It interrupts a later update with SIGTERM during
activation, verifies automatic rollback, and then retries the update. A
concurrent provider switch is rejected while the update holds the shared lock.
The harness stubs APT, systemd, network access, Bun, and Cargo; it checks
deployment orchestration without altering host services or compiling a second
release. Its binary fixture emulates ARM64 platform detection in the x86_64
test container; native ARM64 artifact compatibility is checked separately by
release CI.

The deployment regression suite already covers release manifest validation,
archive path safety, commit matching, checksum and provenance failures, verified
cache handling, supported-target selection, installation idempotency helpers,
selected rollback behavior, provider configuration rollback, and service unit
rendering. Rust web tests exercise REST and WebSocket behavior through the
application router with simulated outputs. The frontend job checks generated
contracts, lint, tests, and the production build. The ARM64 jobs build the
workspace, and the release Docker target checks the produced binaries' Trixie
architecture and dynamic dependencies.

At the start of Goal 22, the following end-to-end evidence was missing:

- CI did not start the exact release directory produced for publication. The
  Docker simulator test built a separate runtime image, so it did not prove the
  packaged bundle could launch its CLI, deployment helper, and web server or
  serve that bundle's frontend assets.
- Platform coverage checked the Debian Trixie build container and an ARM64
  runner, but did not execute the release on selected Ubuntu ARM64 userspaces.
  Raspberry Pi OS userspace compatibility was inferred from Debian-family
  compatibility; no Pi hardware check is part of normal CI.
- Deployment tests exercised helper functions and selected script fragments.
  They did not run complete fresh, repeated, and source installations through
  the production administrative entry point in an isolated filesystem.
- Release verification and rollback were covered independently, but a complete
  installed update lifecycle did not exercise download, staging, activation,
  restart, health timeout, rollback, interrupted recovery, and offline cache
  behavior together.
- Provider tests covered selected mocked transitions and rollback. The full
  migration matrix, credential preservation, restart/health failures, and
  concurrency with updates did not run through the installed administrative
  entry point as one isolated workflow.
- Rust integration tests covered API contracts and simulated engine behavior,
  but CI did not launch the packaged application and drive a public API workflow
  from process startup through synthetic audio and clean shutdown.
- Fault injection for optional subsystem startup and shutdown was incomplete;
  there was no bounded repeated-run profile that reported resource and queue
  health.
- CI had no single machine-readable qualification summary connecting test
  results to the source commit, build target, artifact integrity, provenance,
  and known gaps.

The current integration harness does not execute the GitHub binary download
and Sigstore verification path or compile Rust and frontend source inside the
test container. Archive, checksum, manifest, architecture, provenance,
interrupted-download, and missing-publication failures are covered by the
release-verifier tests. Staging exhaustion is simulated with an ENOSPC failure
at archive extraction; the test confirms the partial stage is removed and the
verified cache remains available. Rollback failure and phase diagnostics are
covered by the update tests. The installed binary lifecycle replays
missing-cache, archive-corruption, wrong-architecture, service restart,
release activation failure, SIGTERM rollback and retry cases; it does not
replay every online integrity or provenance fault through a complete update.
The container lifecycle drives the
installed administrative command through librespot, Soloist, and disabled
provider transitions, including credential preservation, injected startup
and health failures, and SIGTERM interruption with restoration. Provider
script tests also cover missing credentials, unsafe state permissions, and
unavailable services. Live Spotify, physical hardware, and Raspberry Pi OS
image behavior remain manual checks.

The release qualification report now records CI job outcomes, runtime checks,
artifact integrity, provenance, and unverified Raspberry Pi OS and hardware
items. The optional three-minute stability profile runs only when selected in
`workflow_dispatch`; a normal run records it as not verified. When selected,
the CI job uploads profile metrics and failure diagnostics even if a profile
criterion fails. The release publisher also uploads its qualification report
before publication when a smoke, compatibility, integrity, or provenance gate
fails, recording the failed and skipped checks.

The checklist below records operator acceptance separately. A checked item
must include a result for the specific release commit; an unrun item remains
unverified.

## Automated qualification

Record the CI run URL and commit for each release candidate.

- [ ] Hosted deployment regression tests pass.
- [ ] CI produces a machine-readable and human-readable qualification report
  for the workflow, including failed or skipped gates; the release report is
  uploaded before immutable publication.
- [ ] The isolated production-admin lifecycle passes fresh and repeated source
  installation, a fresh binary install from a previously verified offline
  cache, missing-cache and corrupt-artifact rejection, wrong-target rejection,
  release-activation failure rollback, offline binary update rollback and
  retry, interrupted-update rollback and recovery, unchanged and successful
  source update, shared provider/update locking, and restart/health failure
  rollback scenarios.
- [ ] Installed Spotify provider transitions preserve credentials and restore
  the prior configuration after injected startup, health, and SIGTERM failures.
- [ ] Rust formatting, Clippy, workspace tests, and x86_64 build pass.
- [ ] ARM64 workspace tests and build pass on GitHub-hosted ARM64.
- [ ] Frontend contract generation, lint, tests, and production build pass.
- [ ] Release bundle validates its manifest, source commit, architecture,
  runtime libraries, checksum, and Sigstore provenance.
- [ ] The actual release bundle passes the ARM64 startup and HTTP smoke test.
- [ ] Debian Trixie and Ubuntu 26.04 ARM64 userspace checks pass; Raspberry Pi
  OS userspace compatibility is reported as unverified by hosted CI.
- [ ] If selected, the extended stability profile completes without API or
  state errors and reports resource, queue, Spotify-mock, and shutdown results.
- [ ] The qualification report identifies automated results, static checks,
  failures, and unverified items without secrets.

## Manual installation acceptance

- [ ] Install this commit on a fresh supported ARM64 system using the documented
  installer command.
- [ ] Confirm librespot is selected by default and protected configuration and
  installation metadata are present.
- [ ] Confirm the web and Spotify services start and remain active after reboot.
- [ ] Open the dashboard from the local network and confirm its assets load.
- [ ] Repeat installation and confirm saved settings and credentials remain
  unchanged and unchanged services are not restarted.
- [ ] Record the OS release, architecture, commit, command output, and relevant
  service logs.

## Manual Spotify acceptance

- [ ] Discover the LumiStripe Spotify Connect receiver from a supported client.
- [ ] Authenticate and play audio through the selected receiver.
- [ ] Confirm native PCM analysis is active and telemetry reaches the dashboard.
- [ ] Confirm audio reaches the configured speaker output and remains synchronized.
- [ ] Switch providers or disable Spotify only if the installation requires it;
  verify one receiver is active and existing credentials remain available.
- [ ] Record provider state and service logs without recording credentials or
  authentication tokens.

## Manual hardware acceptance

- [ ] Validate the configured GPIO or SPI LED controller and LED count.
- [ ] Confirm Bluetooth discovery, pairing, and audio routing where applicable.
- [ ] Check audio synchronization under representative playback.
- [ ] Check thermal behavior and real runtime stability under sustained use.
- [ ] Record board, controller, OS, and test duration without publishing unique
  device identifiers.

## Recovery acceptance

- [ ] Update from a known-good installed release and confirm the new commit is
  active only after health succeeds.
- [ ] Exercise the documented rollback path and confirm the previous release,
  checkout, configuration, and services are restored.
- [ ] Confirm recovery behavior when the network is unavailable and a verified
  cached artifact is used.
- [ ] Confirm configuration restoration and service recovery after an
  interrupted update.
- [ ] Record whether rollback completed or manual recovery was required.

## Known validation limitations

- Hosted CI does not run a Raspberry Pi OS image. Debian Trixie ARM64 results
  provide a compatibility signal for its userspace, but Pi OS packaging and
  Pi firmware, GPIO, SPI, Bluetooth radio, audio devices, power, and thermal
  behavior remain unverified.
- Hosted software tests do not perform live Spotify authentication or playback.
- Hardware acceptance remains a manual checklist and must not be reported as
  complete without results for the specific release commit.
