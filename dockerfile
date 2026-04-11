# Dockerfile - optimized for Railway Docker/railpack deployment
# Key changes: multi-stage build to reduce final image size & RAM usage
# Uses python:3.12-slim with aggressive layer caching and minimal dependencies

# ============ Stage 1: Builder (install everything, then throw away) ============
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# Install only build-time system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
  build-essential \
  pkg-config \
  zlib1g-dev \
  libjpeg-dev \
  zbar-tools \
  libzbar-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Python deps in a virtual env for clean copy
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel \
  && pip install -r requirements.txt

# ============ Stage 2: Runtime (slim final image) ============
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
# Reduce Python memory footprint
ENV PYTHONDONTWRITEBYTECODE=1
ENV MALLOC_TRIM_THRESHOLD_=65536

# Install only runtime system deps (no build-essential!)
RUN apt-get update && apt-get install -y --no-install-recommends \
  libzbar0 \
  libjpeg62-turbo \
  ffmpeg \
  ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && apt-get clean

# Copy the pre-built venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Non-root user
RUN useradd --create-home --no-log-init bot || true

WORKDIR /app
COPY . /app

# Ensure data and resources directories exist
RUN mkdir -p /app/data /app/resources || true

# Own everything by bot user
RUN chown -R bot:bot /app
USER bot
ENV HOME=/home/bot

# Entry point - uses the new bot.py runner script
CMD ["python", "bot.py"]