#!/bin/bash
set -e

# No more Xvfb, PulseAudio or virtual display needed
echo "Starting bot worker..."
exec python worker.py
