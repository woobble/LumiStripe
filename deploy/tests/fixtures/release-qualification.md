# LumiStripe release qualification: `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`

- Rust toolchain: `1.99.1`
- Bun: `1.4.4`
- Target: `aarch64-unknown-linux-gnu` (aarch64)
- Build environment: `debian:trixie-slim`
- Declared glibc minimum: `2.38`

| Validation | Result | Evidence type | Evidence |
| --- | --- | --- | --- |
| Deployment regression tests | passed | automated | GitHub-hosted deployment-tests job |
| Frontend contract, lint, tests, and build | passed | automated | GitHub-hosted frontend job |
| Rust formatting, Clippy, and x86_64 build | passed | automated | GitHub-hosted rust-engine job |
| Application REST, WebSocket, simulator, and engine tests | passed | automated | Rust workspace test suite on the GitHub-hosted x86_64 runner |
| Opt-in simulator stability profile | not verified | automated when selected | Bounded synthetic audio, mocked Soloist, REST, resource, and shutdown profile |
| Rust ARM64 tests and build | passed | automated | GitHub-hosted rust-engine-arm64 job |
| Release source commit and immutable-tag eligibility | passed | automated | Publisher checkout, ARM64 runner, and existing build-tag guard |
| Debian Trixie ARM64 runtime compatibility | passed | automated | AArch64 Debian Trixie runtime container |
| Actual packaged ARM64 release startup smoke | passed | automated | Packaged release directory, CLI, deployment helper, loopback web health, frontend assets, and clean shutdown |
| Ubuntu 26.04 LTS ARM64 runtime compatibility | passed | automated | Ubuntu 26.04 ARM64 container using the packaged release directory |
| Raspberry Pi OS Trixie ARM64 userspace compatibility | not verified | static inspection | Raspberry Pi OS is Trixie-based; no OS image is executed in hosted CI |
| Release manifest, source commit, ELF architecture, and dependencies | passed | automated | Rust release-artifact validation and Bash ARM64 smoke checks |
| Sigstore build provenance | passed | automated | gh attestation verify pinned to ci.yml and the source commit |
| Live Spotify authentication and playback | not verified | manual acceptance required | No Spotify account or live provider is used by CI |
| Physical Raspberry Pi hardware behavior | not verified | manual acceptance required | GPIO/SPI, Bluetooth radio, audio devices, power, and thermal behavior are not exercised |

## Known limitations

- Raspberry Pi OS Trixie is not independently run in a hosted container; its hardware remains unverified.
- Ubuntu 24.04 LTS is outside the prebuilt binary support matrix; source mode is available.
- Live Spotify authentication and physical audio/LED behavior are manual acceptance checks.
