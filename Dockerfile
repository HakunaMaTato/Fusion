FROM python:3.12-slim AS base

WORKDIR /srv

RUN groupadd --system app && useradd --system --gid app --home-dir /srv app

COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml README.md ./
COPY app ./app
COPY backtest ./backtest
COPY config ./config
RUN pip install --no-cache-dir --no-deps -e .

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=3)" || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
