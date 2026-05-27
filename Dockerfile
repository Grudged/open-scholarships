# Open Scholarships public API — runs on Hetzner behind Coolify's Traefik (Docker-provider labels).
# Code + docs page are baked in; data/ + schema/ are mounted at runtime from a git clone on the
# host (refreshed by cron), so approvals publish via `git pull` without an image rebuild.
FROM python:3.12-slim

WORKDIR /app
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/app ./app
COPY site ./site
ENV OS_SITE_DIR=/app/site
# OS_DATA_DIR and OS_SCHEMA_PATH are provided at runtime, pointing at mounted volumes.

EXPOSE 8932
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8932"]
