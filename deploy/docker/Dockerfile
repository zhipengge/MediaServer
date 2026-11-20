# MediaServer image: Python 3.12 + ffmpeg (HLS/thumbnails) + curl (healthcheck).
#
#   docker build -f deploy/docker/Dockerfile -t mediaserver:1.3.0 ../..
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    TZ=Asia/Shanghai

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg curl gosu ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Runtime dependencies are exported from Pipfile.lock (requirements.txt).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY mediaserver /app/mediaserver
COPY wsgi.py /app/wsgi.py
COPY deploy/docker/entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh && mkdir -p /data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/healthz >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "mediaserver"]
