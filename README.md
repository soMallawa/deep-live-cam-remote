# Deep-Live-Cam Remote

Remote GPU-accelerated real-time face swapping with Deep-Live-Cam, MediaMTX, and WebRTC WHIP/WHEP. A local machine (OBS or droidcam-whip-c) sends camera video to a GPU container on vast.ai, the bridge runs the face swap pipeline, and OBS receives the processed feed back as a browser source.

## Architecture

```text
Local PC / OBS v30+  (or droidcam-whip-c)
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

A web dashboard (`dashboard.py`) runs on port `8080` and manages the bridge process, handles face image uploads, and streams logs in real time.

## Prerequisites

- A vast.ai NVIDIA GPU instance (RTX 3090, A4000, A5000, or similar) with NVENC support.
- OBS Studio 30 or newer on the local PC, or [droidcam-whip-c](https://github.com/soMallawa/droidcam-whip-c) for phone input.
- Public access to `8080/tcp`, `8889/tcp`, and `8189/udp`. Expose `8554/tcp` only if testing RTSP directly.
- The `inswapper_128_fp16.onnx` model file from the [Deep-Live-Cam releases page](https://github.com/hacksider/Deep-Live-Cam).
- A source face image (uploaded via the web dashboard after the container starts).

## Web Dashboard

The container exposes a web dashboard on port `8080`:

```text
http://<vast-ai-ip>:8080
```

| Feature | Description |
|---|---|
| **Source Face** | Upload or drag-and-drop a jpg/png/webp face image. Saved to `/app/source.jpg` in the container — no SSH or volume remount needed. |
| **Bridge Control** | Start and stop `bridge.py` with one click. Shows running state in real time. |
| **Connection URLs** | WHIP, WHEP, and internal RTSP addresses auto-populated from the container host, with copy buttons. |
| **Stream Status** | Live readiness of `cam_in` and `cam_out` from the MediaMTX API, polled every 3 seconds. |
| **Bridge Logs** | Real-time stdout/stderr from `bridge.py` over Server-Sent Events. 500-line history, auto-scroll, color coding. |

## Deploying on vast.ai

vast.ai instances are Docker containers themselves, so `docker compose up` will not work — nested Docker is not available. Use one of the two approaches below.

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
- **Launch mode**: `Interactive shell server, SSH`
- **Extra ports**: `8080/tcp`, `8889/tcp`, `8189/udp`, `8554/tcp`
- **On-start script**:

```bash
mkdir -p /app/models
wget -q -O /app/models/inswapper_128_fp16.onnx "<model-url>"
/app/entrypoint.sh
```

Replace `<model-url>` with the direct download link from the Deep-Live-Cam releases page.

> **Why SSH mode?** vast.ai overwrites the Docker entrypoint to set up its SSH server. The on-start script must call `/app/entrypoint.sh` explicitly at the end, which is what starts MediaMTX and the dashboard. Using "Docker ENTRYPOINT" mode causes the on-start script to be passed as a raw argument to the entrypoint rather than executed by bash.

After the container starts, open `http://<vast-ai-ip>:8080`, upload your source face image via the dashboard, then click **Start Bridge**.

vast.ai pulls your image as the container filesystem — boot time is ~1 minute. After the first boot, use the vast.ai **snapshot** feature to save the instance state (including the model file) so subsequent rents skip the download.

### Option B — Run directly on the instance (no Docker Hub required)

Rent a `nvidia/cuda:12.4.1-devel-ubuntu22.04` instance with **Launch mode: `Interactive shell server, SSH`** and the same ports as above, then use this on-start script:

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

mediamtx /app/mediamtx.yml &
python3.11 /app/dashboard.py
```

After the container starts, open `http://<vast-ai-ip>:8080`, upload your source face image, then click **Start Bridge**.

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
| `8080` | TCP | Web dashboard |
| `8889` | TCP | WHIP ingest and WHEP playback (HTTP signaling) |
| `8189` | UDP | WebRTC ICE media path |
| `8554` | TCP | RTSP direct access (optional, for testing) |

---

## Quick Start (local Docker)

To run locally for testing:

```bash
git clone https://github.com/soMallawa/deep-live-cam-remote
cd deep-live-cam-remote
# place inswapper_128_fp16.onnx in ./models/
docker compose up --build
```

The container prints the endpoints after startup:

```text
Web dashboard:       http://<host-ip>:8080
OBS WHIP URL:        http://<host-ip>:8889/cam_in/whip
WHEP endpoint:       http://<host-ip>:8889/cam_out/whep
```

Open the dashboard, upload a source face, and click **Start Bridge**. The `source.jpg` volume mount in `docker-compose.yml` is optional — the dashboard upload writes directly to `/app/source.jpg` inside the container.

**Send feed from a phone (zero OBS)** using [droidcam-whip-c](https://github.com/soMallawa/droidcam-whip-c):

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

Start streaming. MediaMTX publishes this as `rtsp://127.0.0.1:8554/cam_in` inside the container.

### Receive Processed Video

Add an OBS Browser Source and load `web/viewer.html`.

If the file is served from the same host as MediaMTX, it auto-uses:

```text
http://<host>:8889/cam_out/whep
```

If you open the file locally, pass the WHEP endpoint explicitly:

```text
file:///path/to/web/viewer.html?whep=http://<vast-ai-ip>:8889/cam_out/whep
```

Set the Browser Source resolution to match your stream, for example `1280x720`.

## Direct RTSP Test

The helper script publishes the OBS Virtual Camera through FFmpeg on Windows DirectShow:

```bash
./scripts/stream-in.sh <vast-ai-ip>
```

This pushes to `rtsp://<vast-ai-ip>:8554/cam_in`. For production low-latency browser playback, prefer WHIP ingest on `/cam_in/whip`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `RTSP_IN` | `rtsp://127.0.0.1:8554/cam_in` | Bridge input stream from MediaMTX. |
| `RTSP_OUT` | `rtsp://127.0.0.1:8554/cam_out` | Bridge output stream back to MediaMTX. |
| `SOURCE_IMAGE` | `/app/source.jpg` | Source face image path. |
| `WIDTH` | `1280` | Fallback output width when stream dimensions are unavailable. |
| `HEIGHT` | `720` | Fallback output height when stream dimensions are unavailable. |
| `FPS` | `30` | Output encoder frame rate and keyframe interval basis. |
| `BITRATE` | `4000k` | NVENC H.264 target bitrate. |
| `MAX_INPUT_RETRIES` | `0` | Input retry limit. `0` means retry forever. |
| `DASHBOARD_HOST` | `0.0.0.0` | Dashboard bind address. |
| `DASHBOARD_PORT` | `8080` | Dashboard HTTP port. |
| `MEDIAMTX_API` | `http://127.0.0.1:9997` | MediaMTX REST API base URL used by the dashboard. |

## Troubleshooting

### No face detected in source image

Upload a clear frontal face photo via the dashboard. If uploading via SCP instead:

```bash
scp -P <vast-port> source.jpg root@<vast-ip>:/app/source.jpg
```

Then restart the bridge from the dashboard so it picks up the new image.

### Bridge does not start

Check the dashboard log viewer for Python import errors. The most common cause is a missing model file — confirm `/app/models/inswapper_128_fp16.onnx` exists inside the container.

### No video on cam_in / bridge log shows RTSP timeout

The bridge waits for `cam_in` before producing output. Start OBS WHIP streaming to:

```text
http://<vast-ai-ip>:8889/cam_in/whip
```

Watch the dashboard Stream Status card — `cam_in` will turn green once OBS connects.

### GPU or NVENC not available

Confirm the container can see the GPU:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-devel-ubuntu22.04 nvidia-smi
```

If the bridge log shows `h264_nvenc not available, falling back to libx264`, the container does not have NVENC access. Choose a vast.ai host with an NVIDIA runtime and a GPU that supports NVENC.

### WHEP player does not connect

Use the exact endpoint from the dashboard Connection URLs card:

```text
http://<vast-ai-ip>:8889/cam_out/whep
```

`cam_out` only becomes active after the bridge is running and has received at least one frame on `cam_in`. Verify `8189/udp` is reachable — MediaMTX uses it for the WebRTC media path.

### Latency is too high

Use hardware H.264 encoding in OBS, disable B-frames, set keyframe interval to 1 second, and choose a nearby vast.ai region. Browser playback latency also depends on network route and packet loss.

## References

- Deep-Live-Cam: https://github.com/hacksider/Deep-Live-Cam
- MediaMTX: https://github.com/bluenviron/mediamtx
- droidcam-whip-c: https://github.com/soMallawa/droidcam-whip-c
