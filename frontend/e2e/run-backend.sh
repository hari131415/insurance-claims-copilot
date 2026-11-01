#!/bin/sh
# Start the backend for Playwright with a throwaway database and upload
# directory, so runs never see each other's claims. The backend venv
# provides uvicorn; the mock provider needs no keys.
set -eu
cd "$(dirname "$0")/../../backend"
WORKDIR="$(mktemp -d /tmp/claims-e2e.XXXXXX)"
mkdir -p "${WORKDIR}/uploads"
DATABASE_URL="sqlite:///${WORKDIR}/claims.db" \
UPLOAD_DIR="${WORKDIR}/uploads" \
LLM_PROVIDER="${LLM_PROVIDER:-mock}" \
CORS_ORIGINS="http://localhost:5173" \
exec .venv/bin/uvicorn claims_copilot.main:create_app --factory --port 8000
