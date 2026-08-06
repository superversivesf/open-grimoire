#!/bin/bash
# Restore script for Open Grimoire
# Usage: ./scripts/restore.sh <backup-archive>
# Example: ./scripts/restore.sh /opt/backups/open-grimoire/20260806-120000.tar.gz

set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <backup-archive.tar.gz>"
    echo "Example: $0 /opt/backups/open-grimoire/20260806-120000.tar.gz"
    exit 1
fi

ARCHIVE="$1"
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMP_DIR=$(mktemp -d)

trap "rm -rf ${TEMP_DIR}" EXIT

if [ ! -f "$ARCHIVE" ]; then
    echo "ERROR: Backup file not found: $ARCHIVE"
    exit 1
fi

echo "=== Open Grimoire Restore ==="
echo "  Archive: ${ARCHIVE}"
echo "  Target:  ${SOURCE_DIR}"
echo ""

# Extract backup
echo "Extracting backup..."
tar -xzf "$ARCHIVE" -C "$TEMP_DIR"
BACKUP_NAME=$(ls "$TEMP_DIR")
BACKUP_DIR="${TEMP_DIR}/${BACKUP_NAME}"
echo "  Backup name: ${BACKUP_NAME}"

# Confirm
echo ""
echo "This will OVERWRITE existing data in:"
echo "  - ${SOURCE_DIR}/db-prod/"
echo "  - ${SOURCE_DIR}/data-prod/"
echo "  - ${SOURCE_DIR}/db-test/"
echo "  - ${SOURCE_DIR}/data-test/"
echo ""
read -p "Are you sure? Type 'yes' to continue: " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# Stop containers
echo ""
echo "Stopping containers..."
cd "$SOURCE_DIR"
docker compose stop 2>/dev/null || true

# Restore prod databases
if [ -d "${BACKUP_DIR}/db-prod" ]; then
    echo "Restoring prod databases..."
    mkdir -p "${SOURCE_DIR}/db-prod"
    cp -a "${BACKUP_DIR}/db-prod/." "${SOURCE_DIR}/db-prod/"
fi

# Restore test databases
if [ -d "${BACKUP_DIR}/db-test" ]; then
    echo "Restoring test databases..."
    mkdir -p "${SOURCE_DIR}/db-test"
    cp -a "${BACKUP_DIR}/db-test/." "${SOURCE_DIR}/db-test/"
fi

# Restore prod data
if [ -d "${BACKUP_DIR}/data-prod" ]; then
    echo "Restoring prod data..."
    rm -rf "${SOURCE_DIR}/data-prod"
    cp -a "${BACKUP_DIR}/data-prod" "${SOURCE_DIR}/data-prod"
fi

# Restore test data
if [ -d "${BACKUP_DIR}/data-test" ]; then
    echo "Restoring test data..."
    rm -rf "${SOURCE_DIR}/data-test"
    cp -a "${BACKUP_DIR}/data-test" "${SOURCE_DIR}/data-test"
fi

# Restore configs
if [ -f "${BACKUP_DIR}/config-prod.yaml" ]; then
    echo "Restoring prod config..."
    cp "${BACKUP_DIR}/config-prod.yaml" "${SOURCE_DIR}/config-prod.yaml"
fi
if [ -f "${BACKUP_DIR}/config-test.yaml" ]; then
    echo "Restoring test config..."
    cp "${BACKUP_DIR}/config-test.yaml" "${SOURCE_DIR}/config-test.yaml"
fi

# Restart containers
echo ""
echo "Starting containers..."
docker compose up -d

echo ""
echo "=== Restore Complete ==="
echo "  Prod: http://localhost:8050"
echo "  Test: http://localhost:8051"