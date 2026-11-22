#!/bin/sh
set -e
VOICE="${PIPER_VOICE:-en_US-lessac-medium}"
[ -f "voices/$VOICE.onnx" ] || python -m piper.download_voices "$VOICE" --download-dir voices || echo "WARN: could not fetch Piper voice; replies will be text-only"
exec python -m app.api.main
