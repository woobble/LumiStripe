# LumiStripe web application

This directory contains the Rust Axum web server, React dashboard source, and
generated static build. The REST contract is defined by the Rust application;
the dashboard source is in `frontend/`.

## Build the dashboard

```bash
cd apps/lumistripe-web/frontend
bun install --frozen-lockfile
bun run generate:api
bun run lint
bun run test
bun run build
```

The build output goes to `apps/lumistripe-web/static/`. It is ignored by Git
and served by the Rust web application. Do not commit generated static assets.

Rust request and response types are the REST contract source. Run
`bun run generate:api` after Rust contract changes; it updates
`openapi.json` and `src/generated/api.ts`. Review both generated files and
keep their validation checks clean.

For local Rust simulator startup and settings isolation, see
[the simulator guide](../../docs/simulator.md) and the
[documentation index](../../docs/README.md).
