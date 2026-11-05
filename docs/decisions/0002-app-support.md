# Shared application support package

> Superseded decision record. The Python app-support package and its consumers have been removed. Rust application settings and policy now live in the Rust workspace.

Shared configuration builders live in `lumistripe-app-support`, separate from
`lumistripe-core`. The package may depend on core value objects, but it cannot
depend on hardware, FastAPI, Bluetooth, or frontend libraries.

This keeps core focused on the engine while eliminating duplicated policy
construction in the CLI and simulator.
