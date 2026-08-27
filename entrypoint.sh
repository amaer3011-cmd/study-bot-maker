#!/bin/sh
set -eu

DATA_DIR="${DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR"
chown -R app:app "$DATA_DIR" 2>/dev/null || true

if [ "$#" -eq 0 ]; then
  set -- python main.py
fi

exec gosu app:app "$@"
