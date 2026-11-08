FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HEALTH_PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY config ./config

# run as an unprivileged user; data/ and logs/ are volumes
RUN useradd --create-home --uid 1000 bot && mkdir -p /app/data /app/logs && chown -R bot:bot /app
USER bot
VOLUME ["/app/data", "/app/logs"]
EXPOSE 8080

HEALTHCHECK --interval=2m --timeout=10s --start-period=2m --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5).status == 200 else 1)"

CMD ["python", "-m", "app.main"]
