# M3U Web Picker runs as a Linux container on Docker Desktop (macOS/Windows)
# and Docker Engine (Linux). The application listens on port 9999 in the
# container; Compose controls the host-facing port.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Browser/Roku/Cast playback uses ffmpeg to normalize provider media when a
# client cannot consume the source format directly. Keep ffmpeg in the image so
# the host does not need its own installation.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages before copying the application so dependency layers
# can be reused when only source files change.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

RUN mkdir -p /app/exports /app/data /backups

EXPOSE 9999

# Master Updates run on their own application worker thread, so Waitress request
# threads remain dedicated to navigation, static assets, and live status calls.
CMD ["waitress-serve", "--threads=16", "--channel-request-lookahead=1", "--outbuf-high-watermark=1048576", "--host=0.0.0.0", "--port=9999", "app:app"]
