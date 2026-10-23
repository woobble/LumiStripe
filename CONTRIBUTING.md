# Contributing to LumiStripe

Thanks for helping improve LumiStripe. Changes are welcome through pull requests against `main`.

## Development setup

You need Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Bun 1.4.0. Hardware is not required for the normal test suite; the in-memory stripe and simulator paths are used for development.

Clone the repository with its native dependency submodule:

```bash
git clone --recurse-submodules https://github.com/woobble/LumiStripe.git
cd LumiStripe
LUMISTRIPE_BUILD_EXTENSIONS=audio uv sync --locked --all-extras --dev
cd apps/lumistripe-web/frontend
bun install --frozen-lockfile
cd ../../..
```

The normal test environment uses the explicit audio profile because the DSP
tests exercise the native processor:

Use `uv sync --locked --all-extras --dev` without the variable when you need to
verify the default pure-Python installation, or see
`packages/lumistripe-core/README.md` for the native profile commands.

The optional ALSA development package is needed when building or testing hardware-gain support on Linux. Raspberry Pi-specific GPIO, SPI, Bluetooth, PipeWire, and Nginx behavior should be tested on suitable hardware or called out explicitly in the pull request.

## Checks before opening a pull request

Run the checks relevant to your change. The CI workflow runs all of these:

```bash
uv run ruff check .
uv run mypy \
  packages/lumistripe-core/src/lumistripe \
  apps/lumistripe-cli/src/lumistripe_cli \
  apps/lumistripe-sim/src/lumistripe_sim \
  apps/lumistripe-web/src/lumistripe_web \
  packages/lumistripe-app-support/src/lumistripe_app_support
uv run python -m pytest -q

cd apps/lumistripe-web/frontend
bun run generate:api
bun run lint
bun run test
bun run build
cd ../../..

uv build --all-packages --out-dir dist
```

The frontend build writes generated static assets under the web package's ignored `static` directory. Do not commit generated build output. Keep `uv.lock` and `apps/lumistripe-web/frontend/bun.lock` in sync whenever dependency declarations change.

## Pull requests

Describe the user-visible or operational impact, include validation commands, and mention any hardware or deployment testing that was not available. Keep unrelated formatting or dependency updates out of feature pull requests. Dependency updates should normally be left to Dependabot unless they are required for the change.

By submitting a contribution, you agree that it may be distributed under the repository's MIT license.

## Maintainer settings

The following repository settings keep the checked-in automation effective:

- Protect `main` from force pushes and direct pushes, require pull requests, and require the `Python 3.12`, `Python 3.13`, `Python 3.14`, `Frontend and package build`, `Review dependency changes`, and CodeQL checks before merging.
- Require an approving review and dismiss stale approvals when new commits are pushed.
- Enable Dependabot alerts and security updates, secret scanning with push protection, and private vulnerability reporting when those features are available for the repository.
- Keep GitHub Actions restricted to the permissions declared by each workflow and review any newly introduced action before merging.
