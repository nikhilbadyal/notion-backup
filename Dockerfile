# Stage 1: Build rclone with a Go toolchain that carries the crypto/x509 fix
# (CVE-2026-111943, fixed in Go 1.27.3). The rclone 1.75.2 release binaries are
# built with an older Go. CGO is off, so the result is a static binary that the
# runtime stage can copy without any of the build toolchain.
FROM golang:1.27.3-trixie AS rclone

ARG RCLONE_VER=1.75.2

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --depth 1 --branch "v${RCLONE_VER}" https://github.com/rclone/rclone.git . \
    && CGO_ENABLED=0 go build -trimpath \
    -ldflags "-s -w -X github.com/rclone/rclone/fs.Version=v${RCLONE_VER}" \
    -o /usr/local/bin/rclone .

# Stage 2: Application image, built on the current Debian stable (trixie).
FROM python:3.15-slim-trixie

# Pick up Debian security updates published after the base image was built.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install dependencies, then drop pip/setuptools/wheel: they are only needed
# at build time, and the copies vendored inside pip carry their own CVEs.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m pip uninstall -y pip setuptools wheel \
    && rm -rf /root/.cache/pip

# Set non-root user
RUN useradd --create-home appuser
USER appuser
WORKDIR /home/appuser/app

# Copy application source
COPY --chown=appuser:appuser . .

# Set entrypoint
ENTRYPOINT ["python", "main.py"]
