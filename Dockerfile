# M3U Web Picker runs as a Linux container on Docker Desktop (macOS/Windows)
# and Docker Engine (Linux). The application always listens on port 9999 in
# production; Compose decides which host port maps to it.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python packages before copying the application so dependency layers
# can be reused when only source files change.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

# /backups is the fixed path inside the container. docker-compose.yml maps a
# user-selected host directory to it. The live SQLite DB remains in /app.
RUN mkdir -p /app/exports /app/data /app/debug-data /backups

EXPOSE 9999 9998

# Waitress is used for the normal container. The debug Compose file overrides
# this command and runs `python app.py --dev` on internal port 9998 instead.
CMD ["waitress-serve", "--host=0.0.0.0", "--port=9999", "app:app"]
