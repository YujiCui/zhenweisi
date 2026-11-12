#!/bin/sh
# macOS / Linux: sh start.sh
cd "$(dirname "$0")" && exec python3 studio.py "$@"
