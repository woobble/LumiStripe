# Shared application support package

Shared configuration builders live in `lumistripe-app-support`, separate from
`lumistripe-core`. The package may depend on core value objects, but it cannot
depend on hardware, FastAPI, Bluetooth, or frontend libraries.

This keeps core focused on the engine while eliminating duplicated policy
construction in the CLI and simulator.
