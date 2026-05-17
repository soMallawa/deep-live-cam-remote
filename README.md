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

## Quick Start

Build and run locally or on a vast.ai instance:

```bash
git clone <your-repo-url> deep-live-cam-remote
cd deep-live-cam-remote
cp /path/to/source-face.jpg source.jpg
docker compose up --build
```

The container prints the endpoints after startup:

```text
OBS WHIP URL: http://<vast-ai-ip>:8889/cam_in/whip
WHEP endpoint: http://<vast-ai-ip>:8889/cam_out/whep
```

On vast.ai, expose `8889/tcp` for WebRTC WHIP/WHEP signaling and `8189/udp` for WebRTC ICE media. Expose `8554/tcp` only if you want to test RTSP directly.

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
