#!/bin/bash
# Stream OBS Virtual Camera to the remote RTSP ingest path.
# Usage: ./stream-in.sh <vast-ai-ip>

set -euo pipefail

VAST_IP=${1:?"Usage: $0 <vast-ai-ip>"}

echo "Streaming OBS Virtual Camera to rtsp://${VAST_IP}:8554/cam_in"

ffmpeg -f dshow -i "OBS Virtual Camera" \
  -c:v h264_nvenc -preset p1 -tune ll \
  -b:v 4000k -g 30 -bf 0 \
  -f rtsp "rtsp://${VAST_IP}:8554/cam_in"
