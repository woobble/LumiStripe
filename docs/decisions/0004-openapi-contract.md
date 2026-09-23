# OpenAPI as the REST contract source

FastAPI/Pydantic models are the source of truth for REST request and response
shapes. Frontend REST types are generated from the OpenAPI document rather
than maintained as a second hand-written model hierarchy.

WebSocket and binary preview protocols remain documented separately because
OpenAPI does not describe WebSocket message streams.
