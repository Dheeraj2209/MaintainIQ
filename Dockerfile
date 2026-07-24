# --- Stage 1: build the React/Vite SPA -------------------------------------
FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime -------------------------------------------------
FROM python:3.12-slim AS runtime
WORKDIR /app

# Keep Python lean and predictable in a container.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY models/ ./models/
COPY --from=frontend-build /frontend/dist/ ./frontend/dist/

# maintainiq.db lives at the repo root (src/storage/db.py DEFAULT_DB_PATH) and
# is mounted as a volume in docker-compose.yml so training runs and API
# restarts share the same data.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
