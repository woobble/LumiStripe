# Contributing to LumiStripe

Changes are welcome through pull requests against `main`.

## Development setup

The supported application is native Rust. The root `Cargo.toml` is the
workspace entry point; applications are under `apps/` and shared libraries
under `crates/`. Development requires Rust 1.88 and the Bun version pinned in
`.bun-version`. GPIO, SPI, Bluetooth, and PipeWire hardware are not needed for
the default software checks.

Clone the repository:

```bash
git clone https://github.com/woobble/LumiStripe.git
cd LumiStripe
```

## Checks before opening a pull request

Run the checks relevant to your change. The hosted workflow checks the Rust
workspace, frontend contracts/build, and deployment templates.

```bash
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets
cargo test --locked --workspace
cargo build --locked --workspace

cd apps/lumistripe-web/frontend
bun install --frozen-lockfile
bun run generate:api
git diff --exit-code -- openapi.json src/generated/api.ts
bun run lint
bun run test
bun run build
cd ../../..
```

The frontend build writes ignored assets to
`apps/lumistripe-web/static`; do not commit the generated output. Rust is the
source of REST models and OpenAPI. Regenerate both `openapi.json` and
`src/generated/api.ts` through `bun run generate:api` when the contract
changes.

The deployment regression suite is a Python-only development tool. It checks
shell scripts, systemd templates, and Docker deployment behavior; Python is
not part of the production application. `.python-version` pins the interpreter
used by this harness. Run it with Python and uv:

```bash
uv run --no-project --with pytest python -m pytest -q deploy/tests
```

Do not run checks on the persistent Raspberry Pi runner or physical hardware
unless the change specifically requires device validation and that work is
authorized. For hardware-dependent changes, record exactly what was and was
not exercised.

## Architecture and deployment

The Rust web application, engine, CLI, and deployment helper live in
`apps/`. Shared Rust libraries live in `crates/`, and shared fixtures live in
`tests/fixtures/`. The React frontend lives in
`apps/lumistripe-web/frontend/`; its production build is served from
`apps/lumistripe-web/static/`. See the
[architecture guide](docs/architecture.md), [deployment guide](deploy/README.md),
and [simulator guide](docs/simulator.md).

Python app packages, the Python CLI, native C extension modules, and their
production deployment profiles have been retired. Existing configuration with
`LUMI_WEB_BACKEND=python` fails with migration instructions before the
installer or updater changes environment or application data. The user must
set the saved value to `rust` manually and rerun the operation.

## Pull requests

Describe the user-visible or operational impact, include the validation
commands you ran, and mention hardware or deployment checks that were not
available. Keep unrelated formatting and dependency updates out of feature
pull requests.

By submitting a contribution, you agree that it may be distributed under the
repository's MIT license.

## Maintainer settings

- Protect `main` from force pushes and direct pushes, require pull requests,
  and require the Rust, frontend contract/build, deployment regression,
  dependency review, and CodeQL checks before merging.
- Require an approving review and dismiss stale approvals when new commits are
  pushed.
- Enable Dependabot alerts and security updates, secret scanning with push
  protection, and private vulnerability reporting where available.
- Keep GitHub Actions restricted to the permissions declared by each workflow
  and review new actions before merging.
