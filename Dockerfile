# Stage 1: Pinned rclone binary. The official image is multi-arch and its
# static binary is copied out, so curl, unzip and the build toolchain never
# reach the runtime image.
FROM rclone/rclone:1.75.2 AS rclone

# Stage 2: Application image, built on the current Debian stable (trixie).
FROM python:3.14-slim-trixie

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
