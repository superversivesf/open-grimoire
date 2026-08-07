#!/bin/bash
# Deploy script for Open Grimoire
# Usage:
#   ./scripts/deploy.sh prod   — build + deploy production (port 8050)
#   ./scripts/deploy.sh test   — build + deploy test/staging (port 8051)
#   ./scripts/deploy.sh all    — build + deploy both
#   ./scripts/deploy.sh stop   — stop all
#   ./scripts/deploy.sh status — check status

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SOURCE_DIR"

ACTION="${1:-status}"

check_host_ollama() {
    echo "Checking Ollama on host..."
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "  Ollama: OK"
    else
        echo "  Ollama: NOT RUNNING on localhost:11434"
        echo "  Start it with: ollama serve"
        exit 1
    fi
}

ensure_admin_user() {
    local ENV_NAME=$1
    local DB_DIR=$2
    local CONFIG=$3

    echo "Checking admin user for ${ENV_NAME}..."
    if [ ! -f "${DB_DIR}/shared.sqlite" ]; then
        echo "  Creating shared DB and admin user..."
        mkdir -p "${DB_DIR}"
        ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
        local RESULT
        RESULT=$(python -c "
import sys
sys.path.insert(0, '${SOURCE_DIR}')
from app.cli.bootstrap import ensure_admin_user
from pathlib import Path
password = ensure_admin_user(Path('${DB_DIR}'), admin_password='${ADMIN_PASSWORD}' or None)
print(password or '')
" 2>/dev/null)
        if [ -n "${RESULT}" ]; then
            echo "  Created admin user"
            echo "  Admin password: ${RESULT}"
            echo "  (Save this now — it will not be shown again. Change it after first login.)"
        else
            echo "  Admin user exists"
        fi
    else
        echo "  DB exists, skipping user creation"
    fi
}

case "$ACTION" in
    prod)
        echo "=== Deploying PRODUCTION ==="
        check_host_ollama
        ensure_admin_user "prod" "${SOURCE_DIR}/db-prod" "${SOURCE_DIR}/config-prod.yaml"
        echo ""
        echo "Building and starting prod container..."
        docker compose up -d --build prod
        echo ""
        echo "=== Production Deployed ==="
        echo "  URL: http://localhost:8050"
        echo "  Logs: docker compose logs -f prod"
        ;;

    test)
        echo "=== Deploying TEST/STAGING ==="
        check_host_ollama
        ensure_admin_user "test" "${SOURCE_DIR}/db-test" "${SOURCE_DIR}/config-test.yaml"
        echo ""
        echo "Building and starting test container..."
        docker compose up -d --build test
        echo ""
        echo "=== Test/Staging Deployed ==="
        echo "  URL: http://localhost:8051"
        echo "  Logs: docker compose logs -f test"
        ;;

    all)
        echo "=== Deploying ALL (prod + test) ==="
        check_host_ollama
        ensure_admin_user "prod" "${SOURCE_DIR}/db-prod" "${SOURCE_DIR}/config-prod.yaml"
        ensure_admin_user "test" "${SOURCE_DIR}/db-test" "${SOURCE_DIR}/config-test.yaml"
        echo ""
        echo "Building and starting containers..."
        docker compose up -d --build
        echo ""
        echo "=== All Services Deployed ==="
        echo "  Prod: http://localhost:8050"
        echo "  Test: http://localhost:8051"
        echo "  Logs: docker compose logs -f"
        ;;

    stop)
        echo "Stopping all containers..."
        docker compose stop
        echo "Stopped."
        ;;

    down)
        echo "Stopping and removing all containers..."
        docker compose down
        echo "Done."
        ;;

    status)
        echo "=== Open Grimoire Status ==="
        echo ""
        docker compose ps 2>/dev/null || echo "  Docker compose not running"
        echo ""
        echo "Ollama: $(curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f\"OK ({len(d[\"models\"])} models)\")' 2>/dev/null || echo 'NOT RUNNING')"
        echo ""
        echo "Directories:"
        for d in data-prod db-prod data-test db-test; do
            if [ -d "${SOURCE_DIR}/$d" ]; then
                SIZE=$(du -sh "${SOURCE_DIR}/$d" 2>/dev/null | cut -f1)
                echo "  $d: $SIZE"
            else
                echo "  $d: not created"
            fi
        done
        ;;

    *)
        echo "Usage: $0 {prod|test|all|stop|down|status}"
        exit 1
        ;;
esac