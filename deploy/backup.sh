#!/bin/sh
set -eu
cd /opt/emerald-rozalia-email-centre-python
mkdir -p backups
stamp=$(date +%Y%m%d-%H%M%S)
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "backups/database-$stamp.sql.gz"
find backups -type f -mtime +14 -delete

