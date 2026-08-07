from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import router
from .auth import SESSION_COOKIE, PairingAuth
from .runtime import LumiStripeRuntime, RuntimeSettings

RuntimeFactory = Callable[[RuntimeSettings], LumiStripeRuntime]


def create_app(
    settings: RuntimeSettings | None = None,
    *,
    runtime_factory: RuntimeFactory = LumiStripeRuntime,
    static_dir: Path | None = None,
    pairing_code: str | None = None,
) -> FastAPI:
    resolved_settings = settings or RuntimeSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = runtime_factory(resolved_settings)
        app.state.runtime = runtime
        runtime.start()
        try:
            yield
        finally:
            runtime.stop()

    app = FastAPI(title="LumiStripe", lifespan=lifespan)
    app.state.access = PairingAuth(pairing_code)

    @app.middleware("http")
    async def require_pairing(request: Request, call_next):
        public_api_paths = {
            "/api/health",
            "/api/auth/status",
            "/api/auth/pair",
        }
        if (
            request.url.path.startswith("/api/")
            and request.url.path not in public_api_paths
            and not app.state.access.authenticated(request.cookies.get(SESSION_COOKIE))
        ):
            return JSONResponse(
                {"detail": "Pairing required."},
                status_code=401,
            )
        return await call_next(request)

    app.include_router(router)

    resolved_static_dir = static_dir or Path(__file__).with_name("static")
    if (resolved_static_dir / "index.html").is_file():
        app.frontend("/", directory=resolved_static_dir, fallback="index.html")
    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LumiStripe web dashboard backend")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--pixels", type=_positive_int, default=80)
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="Use physical output instead of the safe in-memory simulation",
    )
    parser.add_argument(
        "--output-backend",
        choices=("spi", "gpio"),
        default="spi",
        help="Hardware output backend (default: spi)",
    )
    parser.add_argument("--spi-device", default="/dev/spidev0.0")
    parser.add_argument("--spi-speed", type=_positive_int, default=1_000_000)
    parser.add_argument("--spi-device-2")
    parser.add_argument("--spi-speed-2", type=_positive_int)
    parser.add_argument("--chip", default="/dev/gpiochip0")
    parser.add_argument("--data-pin", type=int, default=14)
    parser.add_argument("--clock-pin", type=int, default=15)
    parser.add_argument(
        "--audio-source",
        choices=("auto", "off", "demo", "mic"),
        default="auto",
        help="Dynamic-mode audio source (auto uses demo in simulation and mic on hardware)",
    )
    parser.add_argument("--audio-device")
    parser.add_argument(
        "--settings-file",
        type=Path,
        default=RuntimeSettings().settings_file,
        help="Persistent dashboard settings file (default: ~/.config/lumistripe/settings.json)",
    )
    parser.add_argument(
        "--pairing-code",
        type=_pairing_code,
        help="Require this four-digit code before allowing dashboard access",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.spi_speed_2 is not None and args.spi_device_2 is None:
        parser.error("--spi-speed-2 requires --spi-device-2")
    if args.output_backend == "gpio" and args.spi_device_2 is not None:
        parser.error("--spi-device-2 requires --output-backend spi")
    settings = RuntimeSettings(
        pixels=args.pixels,
        hardware=args.hardware,
        output_backend=args.output_backend,
        spi_device=args.spi_device,
        spi_speed_hz=args.spi_speed,
        spi_device_2=args.spi_device_2,
        spi_speed_hz_2=args.spi_speed_2,
        chip=args.chip,
        data_pin=args.data_pin,
        clock_pin=args.clock_pin,
        audio_source=args.audio_source,
        audio_device=args.audio_device,
        settings_file=args.settings_file,
    )
    uvicorn.run(
        create_app(settings, pairing_code=args.pairing_code),
        host=args.host,
        port=args.port,
        workers=1,
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _pairing_code(value: str) -> str:
    if len(value) != 4 or not value.isascii() or not value.isdigit():
        raise argparse.ArgumentTypeError("must contain exactly four digits")
    return value


app = create_app()
