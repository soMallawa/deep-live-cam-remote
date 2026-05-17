#!/bin/bash
set -euo pipefail

echo "=== Deep-Live-Cam Remote ==="
echo "Starting MediaMTX..."
mediamtx /app/mediamtx.yml &
MEDIAMTX_PID=$!
sleep 3

echo
echo "OBTAIN THE VAST.AI PUBLIC IP OR USE THE VAST.AI PROXY URL"
echo
echo "OBS WHIP URL:        http://<vast-ai-ip>:8889/cam_in/whip"
echo "WHEP endpoint:       http://<vast-ai-ip>:8889/cam_out/whep"
echo "Web dashboard:       http://<vast-ai-ip>:8080"
echo "Expose UDP 8189 for WebRTC ICE when running behind Docker."
echo

echo "Starting dashboard..."
python3.11 /app/dashboard.py &
DASHBOARD_PID=$!

shutdown() {
  echo "Stopping..."
  kill "$DASHBOARD_PID" "$MEDIAMTX_PID" 2>/dev/null || true
  wait "$DASHBOARD_PID" "$MEDIAMTX_PID" 2>/dev/null || true
}

trap shutdown SIGINT SIGTERM

wait -n "$MEDIAMTX_PID" "$DASHBOARD_PID" || true
shutdown
