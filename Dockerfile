# --- Stage 1: build the React/Vite SPA -------------------------------------
FROM node:22-alpine AS frontend-build
WORKDIR /frontend

# Extra CA certs for corporate TLS inspection (e.g. Zscaler). certs/ is
# gitignored and may hold only .gitkeep, in which case this is a no-op.
COPY certs/ /tmp/certs/
RUN cat /tmp/certs/*.crt > /usr/local/share/extra-ca.pem 2>/dev/null || true
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/extra-ca.pem

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

# Same extra CA certs, added to the system store so pip and requests trust them.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt     REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt     SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

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
