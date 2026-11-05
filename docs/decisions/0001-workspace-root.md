# Workspace-only root package

> Historical decision record. The Python workspace described below has been retired. The Rust workspace and Bun frontend are the current build roots; see the current README and architecture guide.

The repository root is a uv workspace and development-tooling entry point. It
does not install the CLI, simulator, or web application as one umbrella
runtime package. Applications declare their own dependencies and can be
installed independently.

This prevents a headless or simulator deployment from pulling in web-only
dependencies and makes deployment intent visible in each application manifest.
