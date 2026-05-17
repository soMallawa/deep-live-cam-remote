# Deep-Live-Cam Remote

Remote GPU-accelerated real-time face swapping with Deep-Live-Cam, MediaMTX, and WebRTC WHIP/WHEP. The local machine sends camera video from OBS to a GPU container on vast.ai, the bridge runs the face swap pipeline, and OBS receives the processed feed back as a browser source.

## Architecture

```text
Local PC / OBS v30+
  WHIP output
      |
      | http://<vast-ip>:8889/cam_in/whip
      v
vast.ai GPU container
  MediaMTX
    WHIP ingest -> RTSP publisher
      |
      | rtsp://127.0.0.1:8554/cam_in
      v
  bridge.py
    OpenCV RTSP read -> Deep-Live-Cam CUDA face swap -> NVENC RTSP push
      |
      | rtsp://127.0.0.1:8554/cam_out
      v
  MediaMTX
    RTSP publisher -> WHEP playback
      |
      | http://<vast-ip>:8889/cam_out/whep
      v
Local PC / OBS Browser Source
```

MediaMTX handles ICE, DTLS, SRTP, WHIP, and WHEP. Deep-Live-Cam only sees RTSP input and output.

## Prerequisites

- A vast.ai NVIDIA GPU instance with Docker and NVIDIA Container Toolkit support.
- A GPU with NVENC support, such as an RTX 3090, A4000, A5000, or similar.
- OBS Studio 30 or newer on the local PC.
- A source face image mounted or copied to `/app/source.jpg`.
- Public access to `8889/tcp` and `8189/udp`. Expose `8554/tcp` only if testing RTSP directly.

## Deploying on vast.ai

vast.ai instances are Docker containers themselves, so `docker compose up` will not work there — nested Docker is not available. Use one of the two approaches below.

### Option A — Docker Hub image (recommended)

Build the image on your local machine and push it to Docker Hub:

```bash
git clone https://github.com/soMallawa/deep-live-cam-remote
cd deep-live-cam-remote
docker build -t yourdockerhub/deep-live-cam-remote:latest .
docker push yourdockerhub/deep-live-cam-remote:latest
```

When renting on vast.ai:

- **Image**: `yourdockerhub/deep-live-cam-remote:latest`
- **Extra ports**: `8889/tcp`, `8189/udp`, `8554/tcp`
- **On-start script**:

```bash
wget -q -O /app/models/inswapper_128_fp16.onnx "<model-url>" && /app/entrypoint.sh
```

Replace `<model-url>` with the download link from the [Deep-Live-Cam releases page](https://github.com/hacksider/Deep-Live-Cam).

Upload your source face with `scp` after the instance starts:

```bash
scp -P <vast-port> source.jpg root@<vast-ip>:~/source.jpg
# then inside the instance:
cp ~/source.jpg /app/source.jpg
```

vast.ai pulls your image as the container filesystem — boot time is ~1 minute. After the first boot, use the vast.ai **snapshot** feature to save the instance state so subsequent rents skip the model download.

### Option B — Run directly on the instance (no Docker Hub required)

Rent a `nvidia/cuda:12.4.1-devel-ubuntu22.04` instance with the same ports as above, then use this on-start script:

```bash
apt-get update -q && apt-get install -y -q ffmpeg git libgl1 libglib2.0-0 \
  software-properties-common wget xz-utils \
  && add-apt-repository -y ppa:deadsnakes/ppa \
  && apt-get install -y -q python3.11 python3.11-dev python3.11-venv

wget -qO /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py \
  && python3.11 /tmp/get-pip.py

# install mediamtx
wget -qO /tmp/mtx.tar.gz \
  "$(wget -qO- https://api.github.com/repos/bluenviron/mediamtx/releases/latest \
    | python3.11 -c "import json,sys; d=json.load(sys.stdin); \
      print(next(a['browser_download_url'] for a in d['assets'] \
        if 'linux_amd64.tar.gz' in a['name']))")" \
  && tar -xzf /tmp/mtx.tar.gz -C /usr/local/bin mediamtx \
  && chmod +x /usr/local/bin/mediamtx

git clone --depth 1 https://github.com/soMallawa/deep-live-cam-remote /app
git clone --depth 1 https://github.com/hacksider/Deep-Live-Cam /app/Deep-Live-Cam

python3.11 -m pip install -q -r /app/requirements.txt
grep -Ev '^(opencv-python|onnxruntime-gpu|onnxruntime-silicon)' \
  /app/Deep-Live-Cam/requirements.txt \
  | python3.11 -m pip install -q -r /dev/stdin

mkdir -p /app/models
wget -q -O /app/models/inswapper_128_fp16.onnx "<model-url>"
# upload source.jpg via scp then: cp ~/source.jpg /app/source.jpg

mediamtx /app/mediamtx.yml &
python3.11 /app/bridge.py
```

This installs everything from scratch on each boot (~10 minutes). Use Option A for faster iteration.

### Comparison

| | Option A (Docker Hub) | Option B (direct) |
|---|---|---|
| Boot time | ~1 min (pull image) | ~10 min (install everything) |
| Requires local Docker | Yes | No |
| Persistence | vast.ai snapshot | vast.ai snapshot |

### Port reference

| Port | Protocol | Purpose |
|---|---|---|
| `8080` | TCP | Web dashboard (start/stop bridge, upload face, view logs) |
| `8889` | TCP | WHIP ingest and WHEP playback (HTTP signaling) |
| `8189` | UDP | WebRTC ICE media path |
| `8554` | TCP | RTSP direct access (optional, for testing) |

---

## Web Dashboard

The container runs a web dashboard on port `8080` that lets you manage the bridge without SSH access.

```text
http://<vast-ai-ip>:8080
```

Features:

- **Source Face** — upload or drag-and-drop a face image (jpg/png/webp). The image is saved to `/app/source.jpg` inside the container. You do not need to re-mount a volume or restart the container.
- **Bridge Control** — start and stop `bridge.py` with a single click. The button shows the current state and is disabled when the action is not applicable.
- **Connection URLs** — WHIP ingest, WHEP playback, and internal RTSP addresses populated from the container's host, ready to copy.
- **Stream Status** — live readiness of `cam_in` and `cam_out` paths from the MediaMTX API, updated every 3 seconds.
- **Bridge Logs** — real-time output from `bridge.py` delivered over Server-Sent Events with syntax highlighting, a 500-line history buffer, and auto-scroll.

Add `8080/tcp` to the vast.ai extra ports when renting. The dashboard does not require a separate on-start script change — `entrypoint.sh` starts it automatically.

---

## Quick Start (local Docker)

To run locally for testing:

```bash
git clone https://github.com/soMallawa/deep-live-cam-remote
cd deep-live-cam-remote
cp /path/to/source-face.jpg source.jpg
# place inswapper_128_fp16.onnx in ./models/
docker compose up --build
```

The container prints the endpoints after startup:

```text
Web dashboard:       http://<host-ip>:8080
OBS WHIP URL:        http://<host-ip>:8889/cam_in/whip
WHEP endpoint:       http://<host-ip>:8889/cam_out/whep
```

Open the dashboard to upload a source face image, start the bridge, and monitor logs without needing SSH.

**Send feed from phone (zero OBS)** using [droidcam-whip-c](https://github.com/soMallawa/droidcam-whip-c):

```bash
# Windows GUI
droidcam-whip.exe --gui
# CLI
droidcam-whip.exe 192.168.1.100 rtsp://<host-ip>:8554/cam_in
```

## OBS Setup

### Send Camera to the Container

In OBS 30+, configure WHIP output:

- Server: `http://<vast-ai-ip>:8889/cam_in/whip`
- Bearer token: leave empty
- Video encoder: hardware H.264 if available
- Rate control: CBR
- Keyframe interval: 1 second
- B-frames: disabled if your encoder exposes the option

Start streaming. MediaMTX publishes this as:

```text
rtsp://127.0.0.1:8554/cam_in
```

### Receive Processed Video

Add an OBS Browser Source and load `web/viewer.html`.

If the HTML is served from the same host and port as MediaMTX, it auto-uses:

```text
http://<host>:8889/cam_out/whep
```

If you open the file locally, pass the WHEP endpoint explicitly:

```text
file:///path/to/web/viewer.html?whep=http://<vast-ai-ip>:8889/cam_out/whep
```

Set the Browser Source resolution to match your stream, for example `1280x720`.

## Direct RTSP Test

The helper script shows a direct RTSP publishing path for local testing:

```bash
./scripts/stream-in.sh <vast-ai-ip>
```

This uses the OBS Virtual Camera through FFmpeg on Windows DirectShow and publishes to:

```text
rtsp://<vast-ai-ip>:8554/cam_in
```

For production low-latency browser playback, prefer OBS WHIP ingest on `/cam_in/whip`.

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `RTSP_IN` | `rtsp://127.0.0.1:8554/cam_in` | Bridge input stream from MediaMTX. |
| `RTSP_OUT` | `rtsp://127.0.0.1:8554/cam_out` | Bridge output stream back to MediaMTX. |
| `SOURCE_IMAGE` | `/app/source.jpg` | Source face image path. |
| `WIDTH` | `1280` | Fallback output width when stream dimensions are unavailable. |
| `HEIGHT` | `720` | Fallback output height when stream dimensions are unavailable. |
| `FPS` | `30` | Output encoder frame rate and keyframe interval basis. |
| `BITRATE` | `4000k` | NVENC H.264 target bitrate. |
| `MAX_INPUT_RETRIES` | `0` | Input retry limit. `0` means retry forever. |

## Troubleshooting

### No face detected in source image

Check that `/app/source.jpg` exists, is readable, and contains a clear frontal face. With Docker Compose, the default mount is:

```yaml
./source.jpg:/app/source.jpg:ro
```

### RTSP timeout or bridge waits forever

The bridge starts before OBS publishes. Start OBS WHIP streaming to:

```text
http://<vast-ai-ip>:8889/cam_in/whip
```

Then watch the container logs for `Input stream`.

### GPU or NVENC not available

Confirm the container can see the GPU:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-devel-ubuntu22.04 nvidia-smi
```

If FFmpeg logs mention `h264_nvenc` failure, choose a vast.ai image/host with NVIDIA runtime support and a GPU that supports NVENC.

### WHEP player does not connect

Use the exact endpoint:

```text
http://<vast-ai-ip>:8889/cam_out/whep
```

Make sure the bridge is already publishing `cam_out`. If `viewer.html` is opened from a local file, include the `?whep=` query string.

Also verify that `8189/udp` is reachable. MediaMTX uses `8889/tcp` for WHIP/WHEP HTTP signaling and `8189/udp` for the WebRTC media path.

### Latency is too high

Use hardware H.264 encoding in OBS, disable B-frames, keep the keyframe interval at 1 second, and use a nearby vast.ai region. Browser playback latency also depends on network route and packet loss.

## References

- Deep-Live-Cam: https://github.com/hacksider/Deep-Live-Cam
- MediaMTX: https://github.com/bluenviron/mediamtx
- MediaMTX WebRTC WHIP/WHEP documentation: https://github.com/bluenviron/mediamtx
