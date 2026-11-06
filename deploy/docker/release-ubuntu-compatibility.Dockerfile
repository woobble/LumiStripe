# ARM64 userspace runtime for validating the published bundle on Ubuntu 26.04.
FROM ubuntu:26.04 AS release-ubuntu-compatibility

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        binutils \
        ca-certificates \
        file \
        python3 \
        libasound2t64 \
        libatomic1 \
        libdbus-1-3 \
        libgpiod3 \
        libpipewire-0.3-0t64 \
        libpulse0 \
        libspa-0.2-bluetooth \
        libspa-0.2-modules \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/release_artifact.py /usr/local/libexec/lumistripe/release_artifact.py
COPY deploy/release_smoke.py /usr/local/libexec/lumistripe/release_smoke.py
