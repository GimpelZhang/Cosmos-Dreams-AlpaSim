#!/usr/bin/env bash
source "$HOME/simulation/scripts/env.sh"
[ -f "$ARTIFACTS_DIR/renderer.pid" ] && kill "$(cat "$ARTIFACTS_DIR/renderer.pid")" 2>/dev/null || true
pkill -f "omnidreams.impl.grpc.server" 2>/dev/null || true
