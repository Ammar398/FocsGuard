# FocusGuard server: the API and the portal, served from one address.
#
#   docker build -t focusguard .
#   docker run -p 8000:8000 -e DATABASE_URL=... -e JWT_SECRET_KEY=... focusguard
#
# Only the backend runs here. Camera analysis never does - it runs on each
# person's own computer (desktop_agent), and only numbers are uploaded.

# --- 1. build the portal --------------------------------------------------------
FROM node:22-slim AS portal
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. the API, serving the built portal ----------------------------------------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/shared:/app/backend \
    FOCUSGUARD_PORTAL_DIR=/app/frontend/dist
WORKDIR /app

COPY requirements/base.txt requirements/backend.txt requirements/
RUN pip install -r requirements/backend.txt

COPY shared/ shared/
COPY backend/ backend/
COPY --from=portal /app/frontend/dist frontend/dist

# Never run a web server as root.
RUN useradd --create-home focusguard && chown -R focusguard /app
USER focusguard

EXPOSE 8000
# --proxy-headers: behind the host's load balancer every request would otherwise
# appear to come from one address, and the sign-in rate limiter would lock
# everybody out together after a few typos.
CMD ["sh", "-c", "exec uvicorn focusguard_backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
