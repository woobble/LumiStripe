# syntax=docker/dockerfile:1.7

ARG RUST_VERSION=1.88.0
ARG BUN_VERSION=1.4.4
ARG LUMI_RELEASE_COMMIT=0000000000000000000000000000000000000000

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
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY apps/lumistripe-cli/ ./apps/lumistripe-cli/
COPY apps/lumistripe-engine/ ./apps/lumistripe-engine/
COPY apps/lumistripe-deploy-helper/ ./apps/lumistripe-deploy-helper/
COPY apps/lumistripe-web/Cargo.toml ./apps/lumistripe-web/Cargo.toml
COPY apps/lumistripe-web/src/ ./apps/lumistripe-web/src/
COPY apps/lumistripe-web/tests/ ./apps/lumistripe-web/tests/
COPY crates/ ./crates/
COPY tests/fixtures/ ./tests/fixtures/
RUN cargo build --locked --release \
        --package lumistripe-engine --bin lumistripe-engine \
        --package lumistripe-cli --bin lumistripe \
        --package lumistripe-deploy-helper --bin lumistripe-deploy-helper \
    && cargo build --locked --release \
        --package lumistripe-web --bin lumistripe-web --features bluez,pipewire

# CI export target for a complete, commit-matched ARM64 deployment bundle.
# The frontend and all Rust applications are built from this one checkout.
FROM debian:trixie-slim AS release-bundle

ARG LUMI_RELEASE_COMMIT

WORKDIR /release
COPY --from=rust-build /src/target/release/lumistripe-web ./lumistripe-web
COPY --from=rust-build /src/target/release/lumistripe-engine ./lumistripe-engine
COPY --from=rust-build /src/target/release/lumistripe ./lumistripe
COPY --from=rust-build /src/target/release/lumistripe-deploy-helper ./lumistripe-deploy-helper
COPY --from=frontend /src/apps/lumistripe-web/static/ ./static/
RUN printf '%s\n' "$LUMI_RELEASE_COMMIT" > source-commit \
    && chmod 755 lumistripe-web lumistripe-engine lumistripe lumistripe-deploy-helper \
    && find static -type d -exec chmod 755 {} + \
    && find static -type f -exec chmod 644 {} + \
    && chmod 644 source-commit

# Validate the actual Trixie runtime ABI and dynamic dependencies in the
# release environment, rather than inferring compatibility from cross-builds.
FROM debian:trixie-slim AS release-runtime-check

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        binutils \
        file \
        libasound2 \
        libatomic1 \
        libdbus-1-3 \
        libgpiod3 \
        libpipewire-0.3-0 \
        libpulse0 \
        libspa-0.2-bluetooth \
        libspa-0.2-modules \
    && rm -rf /var/lib/apt/lists/*

COPY --from=release-bundle /release /release
RUN set -eu; \
    test "$(uname -m)" = aarch64; \
    grep -Eq '^[0-9a-f]{40}$' /release/source-commit; \
    for binary in lumistripe-web lumistripe-engine lumistripe lumistripe-deploy-helper; do \
        test -x "/release/$binary"; \
        file "/release/$binary" | grep -q 'ARM aarch64'; \
        readelf -h "/release/$binary" | grep -q 'Machine:.*AArch64'; \
        if ldd "/release/$binary" | tee "/tmp/$binary.ldd" | grep -q 'not found'; then \
            echo "Unresolved Trixie runtime dependency for $binary" >&2; exit 1; \
        fi; \
    done; \
    glibc_version="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"; \
    printf '{"architecture":"aarch64","target":"aarch64-unknown-linux-gnu","distribution":"debian-trixie","glibc_minimum":"%s"}\n' \
        "$glibc_version" > /release-build-info.json

FROM scratch AS release-output
COPY --from=release-runtime-check /release /release
COPY --from=release-runtime-check /release-build-info.json /release-build-info.json

FROM debian:trixie-slim AS soloist

ARG TARGETARCH
ARG LUMI_SOLOIST_FIXTURE=0

ENV DEBIAN_FRONTEND=noninteractive

COPY --from=rust-build \
    /src/target/release/lumistripe-deploy-helper \
    /usr/local/bin/lumistripe-deploy-helper

RUN if [ "${LUMI_SOLOIST_FIXTURE}" = "1" ]; then \
        mkdir -p /tmp/soloist \
        && printf '#!/bin/sh\nexit 0\n' > /tmp/soloist/soloist \
        && chmod 755 /tmp/soloist/soloist; \
    else \
        apt-get update \
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
        && rm -rf /tmp/soloist/soloist.tar.gz; \
    fi

FROM debian:trixie-slim AS runtime-common

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

COPY --from=rust-build /src/target/release/lumistripe-web /usr/local/bin/lumistripe-web
COPY --from=rust-build /src/target/release/lumistripe-engine /usr/local/bin/lumistripe-engine
COPY --from=rust-build /src/target/release/lumistripe /usr/local/bin/lumistripe-cli
COPY --from=rust-build /src/target/release/lumistripe-deploy-helper /usr/local/bin/lumistripe-deploy-helper
COPY --from=frontend \
    /src/apps/lumistripe-web/static/ \
    /opt/lumistripe/static/

RUN groupadd --system --gid 10001 lumistripe \
    && useradd --system --uid 10001 --gid lumistripe \
        --home-dir /var/lib/lumistripe --no-create-home lumistripe \
    && chown -R lumistripe:lumistripe /var/lib/lumistripe

WORKDIR /opt/lumistripe
USER 10001:10001

# Optional migration target for the development-only Soloist sidecar. Building
# the default runtime image never downloads or includes Soloist.
FROM runtime-common AS runtime-soloist
COPY --from=soloist /tmp/soloist/soloist /usr/local/bin/soloist
COPY deploy/docker/spotify-entrypoint.sh /usr/local/bin/lumistripe-spotify-entrypoint
RUN chmod 755 /usr/local/bin/lumistripe-spotify-entrypoint

FROM runtime-common AS runtime

ENTRYPOINT ["/usr/local/bin/lumistripe-web"]
CMD ["--simulate", "--host", "127.0.0.1", "--port", "8000", "--static-dir", "/opt/lumistripe/static", "--settings-file", "/var/lib/lumistripe/settings.json"]

HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=30s \
    CMD curl --fail --silent http://127.0.0.1:8000/api/health >/dev/null || exit 1
