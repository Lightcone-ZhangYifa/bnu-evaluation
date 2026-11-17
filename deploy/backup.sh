#!/bin/sh
# Run from the repository root. Existing integrations may keep this entry point.
set -eu
PYTHON=${PYTHON:-python3}
exec "$PYTHON" manage.py backup "${1:?用法: sh deploy/backup.sh /absolute/private/backup-directory}"
