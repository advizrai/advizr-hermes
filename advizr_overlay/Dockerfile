# Advizr Hermes engine — slim image that runs ONLY the advizr_overlay FastAPI
# service (POST /v1/chat SSE, POST /v1/run, GET /health). This is deliberately
# NOT the upstream full Dockerfile (s6 + node + playwright + gateways): the
# overlay disables native tools / memory / gateway, so it needs only the core
# hermes-agent package + fastapi/uvicorn (both are core deps in pyproject).
#
# Build context = repo root (set via railway.json build.dockerfilePath), so
# `COPY . .` brings the whole hermes package for `pip install -e .`.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git: some deps resolve VCS refs; build-essential: psutil/Pillow native bits.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# pyproject's `readme = "README.md"` is excluded by .dockerignore (*.md); create
# an empty placeholder so the build backend resolves during the editable install.
RUN touch README.md

# Core install only (no extras) — matches the overlay's documented local run.
RUN pip install -e .

ENV PORT=8000
EXPOSE 8000

# Run the overlay server (NOT the hermes CLI). $PORT is injected by Railway.
CMD ["sh", "-c", "uvicorn advizr_overlay.server:app --host 0.0.0.0 --port ${PORT}"]
