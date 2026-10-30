from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api import router
from .auth import SESSION_COOKIE, PairingAuth
from .networking import NetworkManagerService
from .runtime import LumiStripeRuntime, RuntimeSettings
from .system_stats import SystemStatsCollector

RuntimeFactory = Callable[[RuntimeSettings], LumiStripeRuntime]


def create_app(
    settings: RuntimeSettings | None = None,
    *,
    runtime_factory: RuntimeFactory = LumiStripeRuntime,
    static_dir: Path | None = None,
    pairing_code: str | None = None,
    update_state_file: Path | None = None,
    project_dir: Path | None = None,
    system_stats_collector: SystemStatsCollector | None = None,
    network_service: NetworkManagerService | None = None,
) -> FastAPI:
    resolved_settings = settings or RuntimeSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = runtime_factory(resolved_settings)
        # The default production runtime must terminate the process after it
        # has cleaned up hardware following an unexpected worker failure. The
        # service manager then applies its configured restart policy. Custom
        # factories remain injectable for tests and embedded use.
        if runtime_factory is LumiStripeRuntime:
            runtime.set_fatal_exit(os._exit)
        app.state.runtime = runtime
        runtime.start()
        try:
            yield
        finally:
            runtime.stop()

    app = FastAPI(title="LumiStripe", lifespan=lifespan)
    app.state.access = PairingAuth(pairing_code)
    app.state.update_state_file = update_state_file or Path(
        os.environ.get(
            "LUMI_UPDATE_STATE_FILE", "/var/lib/lumistripe/update/status.json"
        )
    )
    app.state.update_project_dir = project_dir or Path(
        os.environ.get("LUMI_PROJECT_DIR", Path(__file__).resolve().parents[4])
    )
    app.state.system_stats_collector = system_stats_collector or SystemStatsCollector()
    app.state.network_service = network_service or NetworkManagerService()

    @app.exception_handler(RequestValidationError)
    async def hide_network_password_validation_input(
        request: Request, exc: RequestValidationError
    ):
        if request.url.path == "/api/network/connect":
            errors = [
                {key: value for key, value in error.items() if key != "input"}
                for error in exc.errors()
            ]
            return JSONResponse(
                jsonable_encoder({"detail": errors}), status_code=422
            )
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def require_pairing(request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.url.path.startswith("/api/")
            and request.method in {"DELETE", "PATCH", "POST", "PUT"}
            and origin is not None
        ):
            try:
                parsed_origin = urlsplit(origin)
            except ValueError:
                parsed_origin = None
            request_host = request.headers.get("host", "")
            if (
                parsed_origin is None
                or parsed_origin.scheme.lower() != request.url.scheme.lower()
                or parsed_origin.netloc.casefold() != request_host.casefold()
                or parsed_origin.path
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                return JSONResponse(
                    {"detail": "Cross-origin API requests are not allowed."},
                    status_code=403,
                )

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
    parser.add_argument(
        "--ssl-certfile",
        type=Path,
        help="Serve HTTPS with this certificate (use with --ssl-keyfile)",
    )
    parser.add_argument(
        "--ssl-keyfile",
        type=Path,
        help="Serve HTTPS with this private key (use with --ssl-certfile)",
    )
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
        choices=("auto", "off", "demo", "mic", "bluetooth", "spotify"),
        default="auto",
        help="Dynamic-mode audio source (auto prefers Bluetooth, then mic, on hardware)",
    )
    parser.add_argument(
        "--enable-spotify",
        action="store_true",
        help="Enable Spotify Connect and Spotify audio routing in simulation mode",
    )
    parser.add_argument("--audio-device")
    parser.add_argument(
        "--settings-file",
        type=Path,
        default=RuntimeSettings().settings_file,
        help="Persistent dashboard settings file (default: ~/.config/lumistripe/settings.json)",
    )
    parser.add_argument(
        "--ignore-saved-stripes",
        action="store_true",
        help="Use CLI output settings for this run instead of the saved stripe topology",
    )
    parser.add_argument(
        "--pairing-code",
        type=_pairing_code,
        help="Require this four-digit code before allowing dashboard access",
    )
    parser.add_argument(
        "--log-level",
        choices=("trace", "debug", "info", "warning", "error", "critical"),
        default="info",
        help="Uvicorn/application log verbosity",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.spi_speed_2 is not None and args.spi_device_2 is None:
        parser.error("--spi-speed-2 requires --spi-device-2")
    if args.output_backend == "gpio" and args.spi_device_2 is not None:
        parser.error("--spi-device-2 requires --output-backend spi")
    if (args.ssl_certfile is None) != (args.ssl_keyfile is None):
        parser.error("--ssl-certfile and --ssl-keyfile must be used together")
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
        spotify_enabled=args.enable_spotify,
        settings_file=args.settings_file,
        ignore_saved_stripes=args.ignore_saved_stripes,
    )
    uvicorn.run(
        create_app(settings, pairing_code=args.pairing_code),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
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
