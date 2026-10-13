# Disposable Linux host for the headless install + self-update e2e (scripts/e2e/self-update/README.md).
# No desktop, no Claude login, no API key: `claude` is a stub, notifications go to a file.
FROM debian:bookworm-slim

# cron: the scheduler a headless Linux install uses (only `crontab` is exercised; no daemon runs).
# python3 + tzdata: the harness itself (stdlib only). The engine's own Python comes from uv.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash ca-certificates cron curl git procps python3 tzdata \
 && rm -rf /var/lib/apt/lists/*

# install.sh would curl uv from astral.sh; pre-installing it keeps the run reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /usr/local/bin/

# The scratch repos under /work are owned by the host user; each scenario has its own HOME,
# so the "dubious ownership" opt-out goes in the system config.
RUN git config --system safe.directory '*'

# A normal user: crontab, ~/.local and ~/.claude all live under this account only.
RUN useradd --create-home --shell /bin/bash scout
USER scout
ENV UV_CACHE_DIR=/work/uv-cache \
    UV_PYTHON_INSTALL_DIR=/work/uv-python
WORKDIR /home/scout
