#!/bin/bash
set -e

# Create dbus directory and start dbus-daemon to avoid dbus errors
mkdir -p /run/dbus
dbus-daemon --system --fork || true

# ── Virtual Display (needed for Chromium audio pipeline even in headless mode) ──
Xvfb :99 -screen 0 1280x720x24 -ac &
export DISPLAY=:99
sleep 1

# ── PulseAudio with a null (virtual) sink ───────────────────────────────────────
pulseaudio --start \
    --exit-idle-time=-1 \
    --daemonize=true \
    --log-level=warn
sleep 1

# Create a virtual null sink — ffmpeg will record from its monitor
pactl load-module module-null-sink sink_name=audicle_sink sink_properties=device.description=AudicleSink || true
pactl set-default-sink audicle_sink || true

echo "✅ Virtual display (:99) and PulseAudio (audicle_sink) ready"

# ── Start the bot worker ─────────────────────────────────────────────────────────
exec python worker.py
