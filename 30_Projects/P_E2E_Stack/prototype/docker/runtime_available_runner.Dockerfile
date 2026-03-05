FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN set -eux; \
    apt-get update; \
    if apt-cache show libasound2t64 >/dev/null 2>&1; then audio_pkg=libasound2t64; else audio_pkg=libasound2; fi; \
    apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        jq \
        make \
        python3 \
        "$audio_pkg" \
        libglib2.0-0 \
        libglu1-mesa \
        libgtk-3-0 \
        libnss3 \
        libsdl2-2.0-0 \
        libvulkan1 \
        libxcursor1 \
        libxi6 \
        libxinerama1 \
        libxrandr2 \
        libxss1 \
        libxtst6 \
        xvfb; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /work/30_Projects/P_E2E_Stack/prototype
