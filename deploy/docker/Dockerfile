# syntax=docker/dockerfile:1.7

ARG RUST_VERSION=1.88.0
ARG BUN_VERSION=1.4.4

FROM oven/bun:${BUN_VERSION} AS frontend

WORKDIR /src/apps/lumistripe-web/frontend

COPY apps/lumistripe-web/frontend/package.json apps/lumistripe-web/frontend/bun.lock ./
RUN bun install --frozen-lockfile

COPY apps/lumistripe-web/frontend/ ./
RUN bun run build

FROM rust:${RUST_VERSION}-slim-trixie AS rust-build

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        ca-certificates \
        libasound2-dev \
        libclang-dev \
        libdbus-1-dev \
        libpipewire-0.3-dev \
        libpulse-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY rust/ ./rust/
RUN cargo build --locked --release --manifest-path rust/Cargo.toml \
        --package lumistripe-engine --bin lumistripe-engine \
        --package lumistripe-cli --bin lumistripe \
        --package lumistripe-deploy-helper --bin lumistripe-deploy-helper \
    && cargo build --locked --release --manifest-path rust/Cargo.toml \
        --package lumistripe-web --bin lumistripe-web --features bluez,pipewire

FROM debian:trixie-slim AS soloist

ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive

COPY --from=rust-build \
    /src/rust/target/release/lumistripe-deploy-helper \
    /usr/local/bin/lumistripe-deploy-helper

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && case "${TARGETARCH}" in \
        arm64) archive_arch=arm64 ;; \
        amd64) archive_arch=x86_64 ;; \
        arm) archive_arch=arm32 ;; \
        *) echo "Unsupported Docker architecture: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && mkdir -p /tmp/soloist \
    && /usr/local/bin/lumistripe-deploy-helper soloist-download \
        --url "https://soloist-builds.spotifycdn.com/soloist_release_${archive_arch}.tar.gz" \
        --archive /tmp/soloist/soloist.tar.gz \
        --destination /tmp/soloist/soloist \
    && test -x /tmp/soloist/soloist \
    && rm -rf /tmp/soloist/soloist.tar.gz

FROM debian:trixie-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        dbus \
        libasound2 \
        libatomic1 \
        libdbus-1-3 \
        libgpiod3 \
        libpipewire-0.3-0 \
        libpulse0 \
        libspa-0.2-bluetooth \
        libspa-0.2-modules \
        pipewire-alsa \
        pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/lumistripe/static /var/lib/lumistripe

COPY --from=rust-build /src/rust/target/release/lumistripe-web /usr/local/bin/lumistripe-web
COPY --from=rust-build /src/rust/target/release/lumistripe-engine /usr/local/bin/lumistripe-engine
COPY --from=rust-build /src/rust/target/release/lumistripe /usr/local/bin/lumistripe-cli
COPY --from=rust-build /src/rust/target/release/lumistripe-deploy-helper /usr/local/bin/lumistripe-deploy-helper
COPY --from=frontend \
    /src/apps/lumistripe-web/src/lumistripe_web/static/ \
    /opt/lumistripe/static/
COPY --from=soloist /tmp/soloist/soloist /usr/local/bin/soloist
COPY deploy/docker/spotify-entrypoint.sh /usr/local/bin/lumistripe-spotify-entrypoint

RUN chmod 755 /usr/local/bin/lumistripe-spotify-entrypoint \
    && groupadd --system --gid 10001 lumistripe \
    && useradd --system --uid 10001 --gid lumistripe \
        --home-dir /var/lib/lumistripe --no-create-home lumistripe \
    && chown -R lumistripe:lumistripe /var/lib/lumistripe

WORKDIR /opt/lumistripe
USER 10001:10001

ENTRYPOINT ["/usr/local/bin/lumistripe-web"]
CMD ["--simulate", "--host", "127.0.0.1", "--port", "8000", "--engine-binary", "/usr/local/bin/lumistripe-engine", "--static-dir", "/opt/lumistripe/static", "--settings-file", "/var/lib/lumistripe/settings.json"]

HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=30s \
    CMD curl --fail --silent http://127.0.0.1:8000/api/health >/dev/null || exit 1
