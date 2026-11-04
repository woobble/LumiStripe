# Rust as the REST contract source

**Status:** Accepted  
**Date:** 2026-11-03  
**Supersedes:** [ADR 0004](0004-openapi-contract.md)

## Context

Goal 9 moves the production web backend from FastAPI to Axum while preserving
the existing React client. Keeping Python as the OpenAPI source would leave the
Rust backend dependent on Python for an API artifact and would not prove that
Rust request and response models describe the routes it serves.

## Decision

Rust request and response models and Axum route documentation are the source of
truth for the REST API. Generate `apps/lumistripe-web/frontend/openapi.json`
from the Rust web package, then generate the frontend TypeScript declarations
from that document. Use Utoipa with Axum integration and Rust 1.88 or newer.

The generated Rust document must cover every documented REST operation and
preserve the current JSON property names, required and nullable fields,
constraints, and response status codes. Do not publish a partial or generic
schema that weakens the frontend contracts. Keep FastAPI/Pydantic as the
rollback implementation and compare its behavior against Rust until cutover.
WebSocket and preview protocols remain separately documented.

## Consequences

- Rust REST contract changes update the OpenAPI exporter, generated OpenAPI
  document, and generated TypeScript types in one workflow.
- Contract parity tests compare Rust-generated schemas and runtime behavior to
  the retained FastAPI implementation while both backends coexist.
- The Rust workspace MSRV increases to 1.88 for the selected Utoipa release.
- The OpenAPI source transition is complete only after all routes and schemas
  are represented and frontend generation remains type-safe.
