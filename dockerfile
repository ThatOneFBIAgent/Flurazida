# fuck slim im using the full image
FROM python:3.12

# Install system dependencies
# ffmpeg is required for music playback (discord.py and yt-dlp)
# libffi-dev, libnacl-dev, python3-dev are often needed for PyNaCl build if no wheel exists
RUN apt-get update && apt-get install -y --no-install-recommends \
  build-essential \
  ca-certificates \
  git \
  gcc \
  pkg-config \
  libzbar0 \
  zbar-tools \
  libzbar-dev \
  libjpeg-dev \
  libffi-dev \
  libnacl-dev \
  python3-dev \
  && rm -rf /var/lib/apt/lists/*

RUN ln -s /usr/lib/*/libzbar.so.0 /usr/lib/*/libzbar.so || true

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --upgrade pip setuptools wheel \
  && pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code (yes the entire gp folder)
COPY . .

# Define environment variable for unbuffered output
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# Run the bot
CMD ["python", "src/main.py"]
