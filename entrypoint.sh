#!/bin/sh
set -eu
if [ "${1:-}" = "gunicorn" ]; then
    PROMETHEUS_MULTIPROC_DIR="$(mktemp -d /tmp/fastapi-metrics.XXXXXX)"
    export PROMETHEUS_MULTIPROC_DIR
fi
exec "$@"
