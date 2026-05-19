#!/bin/bash
set -e

if [ "$A4_MODE" = "demo" ]; then
    echo "[A4 TestRunner] Demo mode — Warming up for visual execution (15s)..."
    sleep 15
    echo "[A4 TestRunner] Starting demo pipeline..."
    python /app/scripts/demo_runner.py
    echo "[A4 TestRunner] Demo complete. Container standing by."
    sleep infinity
else
    exec "$@"
fi
