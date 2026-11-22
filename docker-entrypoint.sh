#!/bin/sh
set -e
VOICE="${PIPER_VOICE:-en_US-lessac-medium}"
if python -c "import piper" 2>/dev/null && [ ! -f "voices/$VOICE.onnx" ]; then
  python -m piper.download_voices "$VOICE" --download-dir voices || echo "WARN: could not fetch Piper voice; replies will be text-only"
fi
exec python -m app.api.main
