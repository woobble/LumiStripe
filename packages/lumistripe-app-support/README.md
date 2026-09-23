# LumiStripe application support

This package contains configuration builders and runtime policies shared by
the CLI, simulator, and web application. It deliberately has no hardware,
FastAPI, or frontend dependencies.

Hardware access remains in `lumistripe-core`, while application-specific
argument parsing and HTTP models remain in their respective applications.
