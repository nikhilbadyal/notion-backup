# Stage 1: Build the application with dependencies
FROM python:3.11-slim-bookworm

# Set working directory
WORKDIR /app

# Install system dependencies and rclone
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    unzip \
    && curl https://rclone.org/install.sh | bash \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set non-root user
RUN useradd --create-home appuser
USER appuser
WORKDIR /home/appuser/app

# Copy application source
COPY --chown=appuser:appuser . .

# Set entrypoint
ENTRYPOINT ["python", "main.py"]
