# syntax=docker/dockerfile:1.7

ARG UV_VERSION=0.12.19

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM oven/bun:1.4.0 AS frontend

WORKDIR /src/apps/lumistripe-web/frontend

COPY apps/lumistripe-web/frontend/package.json apps/lumistripe-web/frontend/bun.lock ./
RUN bun install --frozen-lockfile

COPY apps/lumistripe-web/frontend/ ./
RUN bun run build

FROM python:3.12-slim-bookworm AS python-build

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_PREFERENCE=only-system \
    LUMISTRIPE_BUILD_EXTENSIONS=all

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        ca-certificates \
        libasound2-dev \
        libgpiod-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /opt/lumistripe
COPY . .
COPY --from=frontend \
    /src/apps/lumistripe-web/src/lumistripe_web/static \
    /opt/lumistripe/apps/lumistripe-web/src/lumistripe_web/static

RUN uv sync --python /usr/local/bin/python --locked --no-dev --extra hardware-gain

FROM debian:trixie-slim AS soloist

ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        tar \
    && rm -rf /var/lib/apt/lists/* \
    && case "${TARGETARCH}" in \
        arm64) archive_arch=arm64 ;; \
        amd64) archive_arch=x86_64 ;; \
        arm) archive_arch=arm32 ;; \
        *) echo "Unsupported Docker architecture: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && mkdir -p /tmp/soloist \
    && curl --fail --silent --show-error --location --retry 3 --retry-delay 2 \
        "https://soloist-builds.spotifycdn.com/soloist_release_${archive_arch}.tar.gz" \
        --output /tmp/soloist/soloist.tar.gz \
    && tar -xzf /tmp/soloist/soloist.tar.gz -C /tmp/soloist \
    && test -x /tmp/soloist/soloist \
    && install -o root -g root -m 755 /tmp/soloist/soloist /usr/local/bin/soloist \
    && rm -rf /tmp/soloist

FROM python:3.12-slim-trixie AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/lumistripe/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bluez \
        ca-certificates \
        curl \
        dbus \
        libasound2 \
        libasound2-plugins \
        libatomic1 \
        libgpiod3 \
        libportaudio2 \
        libspa-0.2-bluetooth \
        pipewire-alsa \
        pipewire-bin \
        pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/lumistripe

COPY --from=python-build /opt/lumistripe /opt/lumistripe
COPY --from=soloist /usr/local/bin/soloist /usr/local/bin/soloist
COPY deploy/docker/web-entrypoint.sh /usr/local/bin/lumistripe-web-entrypoint
COPY deploy/docker/spotify-entrypoint.sh /usr/local/bin/lumistripe-spotify-entrypoint

RUN chmod 755 \
    /usr/local/bin/lumistripe-web-entrypoint \
    /usr/local/bin/lumistripe-spotify-entrypoint

WORKDIR /opt/lumistripe
