# OpenAPI as the REST contract source

**Status:** Superseded by [ADR 0005](0005-rust-openapi-contract.md)

At the time this decision was made, FastAPI/Pydantic models were the REST
contract source. The current Rust source-of-truth decision is documented in
ADR 0005.

FastAPI/Pydantic models are the source of truth for REST request and response
shapes. Frontend REST types are generated from the OpenAPI document rather
than maintained as a second hand-written model hierarchy.

WebSocket and binary preview protocols remain documented separately because
OpenAPI does not describe WebSocket message streams.
