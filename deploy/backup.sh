#!/bin/sh
# Nightly PostgreSQL backup. Keeps 14 days of gzipped dumps in ./backups.
#
# Credentials are read inside the postgres container rather than from .env on
# the host: the container already has POSTGRES_USER/POSTGRES_DB in its
# environment, and .env cannot be sourced safely from sh because values such as
# MAIL_FROM contain spaces and angle brackets.
set -eu

cd /opt/emerald-rozalia-email-centre-python

mkdir -p backups
stamp=$(date +%Y%m%d-%H%M%S)
target="backups/database-$stamp.sql.gz"

docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$target"

# A failed dump still creates a small gzip header, so reject anything too small.
if [ "$(stat -c %s "$target")" -lt 1000 ]; then
    echo "backup failed: $target is only $(stat -c %s "$target") bytes" >&2
    rm -f "$target"
    exit 1
fi

find backups -type f -name 'database-*.sql.gz' -mtime +14 -delete
echo "backup written: $target"
