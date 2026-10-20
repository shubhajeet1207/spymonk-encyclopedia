# SpyMonk Encyclopedia: the React UI and the FastAPI API in one container.
# Built for Hugging Face Spaces (Docker SDK, port 7860) and runnable anywhere
# Docker is. See docs/DEPLOY_HF_SPACES.md.

# --- Frontend build -----------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/vite.config.js ./
COPY frontend/src ./src
# The UI calls the API on its own origin.
ENV VITE_API_BASE_URL=""
RUN npm run build

# --- Runtime ------------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_TELEMETRY=1

# Noto fonts let PDF reports show Latin and Devanagari text.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# Spaces run containers as UID 1000.
RUN useradd --create-home --uid 1000 user
WORKDIR /app

# CPU-only torch keeps the image a few GB smaller; constraints pin every version.
COPY requirements.txt requirements-local-embeddings.txt constraints.txt ./
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements-local-embeddings.txt -c constraints.txt

COPY --chown=user:user app.py ./
COPY --chown=user:user backend ./backend
COPY --from=frontend --chown=user:user /build/dist ./frontend/dist

USER user
ENV HOME=/home/user \
    HF_HOME=/home/user/.cache/huggingface

# Bake the embedding model into the image so a cold start does not download it.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# The data directory is wiped when a Space restarts; BACKUP_TARGET restores it.
ENV SPYMONK_DATA_DIR=/home/user/spymonk-data \
    SPYMONK_HOSTED=true \
    REBUILD_INDEX_ON_START=true \
    EMBEDDINGS_WARMUP=true \
    PORT=7860

EXPOSE 7860
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
