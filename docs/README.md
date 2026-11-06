# Documentation

## Install and operate LumiStripe

- [Raspberry Pi installation, networking, and audio setup](../deploy/README.md)
- [Rust CLI](../apps/lumistripe-cli/README.md)
- [Browser simulator](simulator.md)

## Configure audio and outputs

- [Audio sources, tuning, and sync calibration](audio.md)
- [Bluetooth and PipeWire setup](../deploy/README.md#pair-a-bluetooth-device)
- [Spotify Connect providers and native pairing](spotify.md)
- [Supported hardware and runtime boundaries](architecture.md#rendering-and-output)

## Understand the system

- [Architecture](architecture.md)
- [Generated REST OpenAPI contract](../apps/lumistripe-web/frontend/openapi.json)
- [REST contract decision](decisions/0005-rust-openapi-contract.md)
- [WebSocket streams](protocols/websocket.md)
- [Preview frame protocol](protocols/preview.md)
- [Engine IPC versions 1, 2, and 3](protocols/rust-engine-ipc.md)
- [Dashboard design notes](design/ui-ux-redesign.md)

## Develop and validate

- [Contributing workflow](../CONTRIBUTING.md)
- [Security policy and vulnerability reporting](../SECURITY.md)
- [Current performance benchmarks](performance.md)
- [Deployment regression tests](../CONTRIBUTING.md#checks-before-opening-a-pull-request)
