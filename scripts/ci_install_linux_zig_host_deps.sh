#!/usr/bin/env bash
# Install system libraries required to link Zig remedy_core on Linux
# (X11 / XTest / AT-SPI). Used by ci.yml and desktop-release.yml.
set -euo pipefail
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  libx11-dev \
  libxtst-dev \
  libatspi2.0-dev \
  libdbus-1-dev \
  libglib2.0-dev \
  pkg-config
