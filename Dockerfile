# Two stages: dependencies are built once in a throwaway stage, the runtime image only carries the
# virtualenv, the app and the scoring config. It runs as a non-root user; SQLite lives in /data.
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.lock


FROM python:3.12-slim AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/srv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /srv app \
    && mkdir /data \
    && chown app:app /data

WORKDIR /srv
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY config ./config

USER app
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=3)" || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
