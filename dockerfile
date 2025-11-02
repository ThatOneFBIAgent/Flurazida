# Dockerfile - use this for Railway Docker/railpack deployment
FROM python:3.12-slim

# Do not buffer stdout/stderr (helps with logs)
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# Install system deps required by some Python packages (zbar for pyzbar, libs for pillow, ffmpeg for media)
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      pkg-config \
      zlib1g-dev \
      libjpeg-dev \
      libzbar0 \
      libzbar-dev \
      ffmpeg \
      ca-certificates \
      git \
    && rm -rf /var/lib/apt/lists/*

# Set working dir
WORKDIR /app

# Copy and install Python deps
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
 && python -m pip install -r /app/requirements.txt

# Copy source
COPY . /app

# Ensure resources folder exists (font, etc.) and is readable
RUN mkdir -p /app/resources || true

# Run as non-root (optional but recommended)
# create user 'bot'
RUN useradd --create-home --no-log-init bot || true
USER bot
ENV HOME=/home/bot
WORKDIR /app

# Run the bot (worker avoids HTTP routing)
# If you want web process (exposed port) use "web:" in Procfile; Discord bot is typically a worker.
CMD ["python", "src/main.py"]
