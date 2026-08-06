#!/bin/bash
# Backup script for Open Grimoire — SQLite + data files
# Usage: ./scripts/backup.sh [backup-dir]
# Default backup dir: /opt/backups/open-grimoire
# Keeps last 7 days of backups

set -euo pipefail

BACKUP_DIR="${1:-/home/jason/backups/open-grimoire}"
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
BACKUP_PATH="${BACKUP_DIR}/${TIMESTAMP}"
RETENTION_DAYS=7

echo "=== Open Grimoire Backup ==="
echo "Source: ${SOURCE_DIR}"
echo "Backup: ${BACKUP_PATH}"

mkdir -p "${BACKUP_PATH}"

# Backup prod databases (SQLite .backup for consistency)
echo ""
echo "Backing up prod databases..."
PROD_DB="${SOURCE_DIR}/db-prod"
if [ -d "${PROD_DB}" ]; then
    mkdir -p "${BACKUP_PATH}/db-prod"
    for db_file in "${PROD_DB}"/*.sqlite; do
        [ -f "$db_file" ] || continue
        db_name=$(basename "$db_file")
        echo "  SQLite backup: ${db_name}"
        sqlite3 "$db_file" ".backup '${BACKUP_PATH}/db-prod/${db_name}'"
    done
else
    echo "  No prod DB directory found"
fi

# Backup test databases
echo ""
echo "Backing up test databases..."
TEST_DB="${SOURCE_DIR}/db-test"
if [ -d "${TEST_DB}" ]; then
    mkdir -p "${BACKUP_PATH}/db-test"
    for db_file in "${TEST_DB}"/*.sqlite; do
        [ -f "$db_file" ] || continue
        db_name=$(basename "$db_file")
        echo "  SQLite backup: ${db_name}"
        sqlite3 "$db_file" ".backup '${BACKUP_PATH}/db-test/${db_name}'"
    done
else
    echo "  No test DB directory found"
fi

# Backup data directories (PDFs, markdown, covers)
echo ""
echo "Backing up prod data..."
PROD_DATA="${SOURCE_DIR}/data-prod"
if [ -d "${PROD_DATA}" ]; then
    echo "  Copying data-prod..."
    cp -a "${PROD_DATA}" "${BACKUP_PATH}/data-prod"
else
    echo "  No prod data directory found"
fi

echo ""
echo "Backing up test data..."
TEST_DATA="${SOURCE_DIR}/data-test"
if [ -d "${TEST_DATA}" ]; then
    echo "  Copying data-test..."
    cp -a "${TEST_DATA}" "${BACKUP_PATH}/data-test"
else
    echo "  No test data directory found"
fi

# Backup config files
echo ""
echo "Backing up configs..."
if [ -f "${SOURCE_DIR}/config-prod.yaml" ]; then
    cp "${SOURCE_DIR}/config-prod.yaml" "${BACKUP_PATH}/"
fi
if [ -f "${SOURCE_DIR}/config-test.yaml" ]; then
    cp "${SOURCE_DIR}/config-test.yaml" "${BACKUP_PATH}/"
fi
if [ -f "${SOURCE_DIR}/docker-compose.yml" ]; then
    cp "${SOURCE_DIR}/docker-compose.yml" "${BACKUP_PATH}/"
fi

# Compress
echo ""
echo "Compressing backup..."
ARCHIVE="${BACKUP_DIR}/${TIMESTAMP}.tar.gz"
tar -czf "${ARCHIVE}" -C "${BACKUP_DIR}" "${TIMESTAMP}"
rm -rf "${BACKUP_PATH}"
echo "  Created: ${ARCHIVE}"

# Clean old backups
echo ""
echo "Cleaning backups older than ${RETENTION_DAYS} days..."
find "${BACKUP_DIR}" -name "*.tar.gz" -mtime +${RETENTION_DAYS} -print -delete
echo "  Done."

# Summary
echo ""
echo "=== Backup Complete ==="
SIZE=$(du -sh "${ARCHIVE}" | cut -f1)
echo "  File: ${ARCHIVE}"
echo "  Size: ${SIZE}"
TOTAL_BACKUPS=$(find "${BACKUP_DIR}" -name "*.tar.gz" | wc -l)
TOTAL_SIZE=$(du -sh "${BACKUP_DIR}" | cut -f1)
echo "  Total backups: ${TOTAL_BACKUPS}"
echo "  Total space: ${TOTAL_SIZE}"