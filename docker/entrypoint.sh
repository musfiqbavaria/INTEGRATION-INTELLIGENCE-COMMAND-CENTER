#!/bin/sh
set -eu
if [ "${1:-}" != "celery" ]; then
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
fi
exec "$@"

