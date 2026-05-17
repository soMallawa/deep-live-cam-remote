#!/usr/bin/env python3
"""
Deep-Live-Cam Remote Bridge
Reads RTSP from MediaMTX, runs DLC face swap, and publishes RTSP back to MediaMTX.
All WebRTC WHIP/WHEP handling is delegated to MediaMTX.
"""

import os
import signal
import subprocess
import sys
import time

import cv2

DLC_PATH = "/app/Deep-Live-Cam"
sys.path.insert(0, DLC_PATH)
sys.path.insert(0, os.path.join(DLC_PATH, "modules"))

RTSP_IN = os.environ.get("RTSP_IN", "rtsp://127.0.0.1:8554/cam_in")
RTSP_OUT = os.environ.get("RTSP_OUT", "rtsp://127.0.0.1:8554/cam_out")
SOURCE_IMAGE = os.environ.get("SOURCE_IMAGE", "/app/source.jpg")
WIDTH = int(os.environ.get("WIDTH", 1280))
HEIGHT = int(os.environ.get("HEIGHT", 720))
FPS = int(os.environ.get("FPS", 30))
BITRATE = os.environ.get("BITRATE", "4000k")
MAX_INPUT_RETRIES = int(os.environ.get("MAX_INPUT_RETRIES", 0))

import modules.globals  # noqa: E402

modules.globals.source_path = SOURCE_IMAGE
modules.globals.target_path = None
modules.globals.output_path = None
modules.globals.frame_processors = ["face_swapper"]
modules.globals.execution_providers = ["CUDAExecutionProvider"]
modules.globals.execution_threads = 2
modules.globals.headless = True
modules.globals.many_faces = False
modules.globals.map_faces = False
modules.globals.mouth_mask = False
modules.globals.live_mirror = False
modules.globals.opacity = 1.0
modules.globals.color_correction = False

from modules.face_analyser import detect_one_face_fast, get_face_analyser  # noqa: E402
from modules.processors.frame.face_swapper import (  # noqa: E402
    apply_post_processing,
    get_face_swapper,
    get_one_face,
    swap_face,
)

source_face = None
stop_requested = False


def log(message):
    print(f"[bridge] {message}", flush=True)


def request_stop(_sig=None, _frame=None):
    global stop_requested
    stop_requested = True
    log("Signal received, shutting down...")


def load_source():
    global source_face

    if not os.path.exists(SOURCE_IMAGE):
        log(f"Source image not found: {SOURCE_IMAGE}")
        log("Place a source.jpg in /app or set SOURCE_IMAGE to a mounted image path.")
        return False

    image = cv2.imread(SOURCE_IMAGE)
    if image is None:
        log(f"Failed to read source image: {SOURCE_IMAGE}")
        return False

    source_face = get_one_face(image)
    if source_face is None:
        log("No face detected in source image.")
        return False

    log(f"Source face loaded from {SOURCE_IMAGE}")
    return True


def open_capture():
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|framedrop;1"
        "|stimeout;5000000"  # 5-second socket timeout so cap.read() never blocks forever
    )
    cap = cv2.VideoCapture(RTSP_IN, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def wait_for_capture():
    attempt = 0
    while not stop_requested:
        cap = open_capture()
        if cap.isOpened():
            return cap

        attempt += 1
        if MAX_INPUT_RETRIES and attempt >= MAX_INPUT_RETRIES:
            cap.release()
            return None

        cap.release()
        log(f"Waiting for RTSP input at {RTSP_IN} (attempt {attempt})...")
        time.sleep(2)
    return None


def _nvenc_available():
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10
        )
        return "h264_nvenc" in r.stdout
    except Exception:
        return False


def start_encoder(rtsp_url, width, height, fps):
    use_nvenc = _nvenc_available()
    if use_nvenc:
        encoder_args = ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll", "-rc", "cbr"]
        log("Using h264_nvenc encoder")
    else:
        encoder_args = ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency"]
        log("h264_nvenc not available, falling back to libx264")

    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "warning",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-", "-an",
        *encoder_args,
        "-b:v", BITRATE, "-maxrate", BITRATE, "-bufsize", BITRATE,
        "-g", str(max(1, fps // 2)),
        "-bf", "0",
        "-pkt_size", "1400",
        "-f", "rtsp", "-rtsp_transport", "tcp",
        rtsp_url,
    ]

    try:
        return subprocess.Popen(cmd, stdin=subprocess.PIPE)
    except Exception as exc:
        log(f"Failed to start FFmpeg encoder: {exc}")
        return None


def stop_encoder(proc):
    if proc is None:
        return

    try:
        if proc.stdin:
            proc.stdin.close()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def get_stream_properties(cap):
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if width <= 0 or height <= 0:
        width, height = WIDTH, HEIGHT
        log(f"Stream did not report dimensions; using fallback {WIDTH}x{HEIGHT}")
    if fps <= 0 or fps > 120:
        fps = FPS
        log(f"Stream did not report a valid FPS; using fallback {FPS}")
    else:
        fps = int(round(fps))
    return width, height, fps


def process_stream(cap, encoder, out_width, out_height, out_fps):
    frame_count = 0
    fps_started = time.time()
    det_interval = max(1, FPS // 6)
    det_count = 0
    cached_target_face = None

    log(f"Processing started. Detection every {det_interval} frames.")
    while not stop_requested:
        ret, frame = cap.read()
        if not ret:
            log("Frame read failed; reconnecting to input stream...")
            return False, encoder

        if frame.shape[1] != out_width or frame.shape[0] != out_height:
            frame = cv2.resize(frame, (out_width, out_height), interpolation=cv2.INTER_LINEAR)

        det_count += 1
        if det_count % det_interval == 0:
            face = detect_one_face_fast(frame)
            if face is not None:
                cached_target_face = face

        if cached_target_face is not None and source_face is not None:
            swapped_bboxes = []
            frame = swap_face(source_face, cached_target_face, frame)
            if hasattr(cached_target_face, "bbox"):
                swapped_bboxes.append(cached_target_face.bbox.astype(int))
            frame = apply_post_processing(frame, swapped_bboxes)

        try:
            encoder.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError):
            log("FFmpeg pipe broke; restarting output encoder...")
            stop_encoder(encoder)
            encoder = start_encoder(RTSP_OUT, out_width, out_height, out_fps)
            if encoder is None:
                return True, None

        frame_count += 1
        elapsed = time.time() - fps_started
        if elapsed >= 5.0:
            log(f"{frame_count} frames | {frame_count / elapsed:.1f} fps")
            frame_count = 0
            fps_started = time.time()

    return True, encoder


def main():
    print("=" * 60, flush=True)
    print("  Deep-Live-Cam Remote Bridge", flush=True)
    print("=" * 60, flush=True)

    log("Loading face analyser...")
    get_face_analyser()
    log("Loading face swapper model...")
    get_face_swapper()

    if not load_source():
        sys.exit(1)

    encoder = None
    cap = None

    try:
        while not stop_requested:
            log(f"Opening RTSP input: {RTSP_IN}")
            cap = wait_for_capture()
            if cap is None:
                log("RTSP input was not available before retry limit.")
                sys.exit(1)

            in_width, in_height, in_fps = get_stream_properties(cap)
            out_width, out_height, out_fps = in_width, in_height, in_fps
            log(f"Input stream: {in_width}x{in_height}@{in_fps}fps; output: {out_width}x{out_height}@{out_fps}fps")

            if encoder is None or encoder.poll() is not None:
                log(f"Starting RTSP output encoder: {RTSP_OUT}")
                encoder = start_encoder(RTSP_OUT, out_width, out_height, out_fps)
                if encoder is None:
                    sys.exit(1)

            keep_encoder, encoder = process_stream(cap, encoder, out_width, out_height, out_fps)
            cap.release()
            cap = None

            if not keep_encoder:
                time.sleep(1)

    except KeyboardInterrupt:
        request_stop()
    finally:
        if cap is not None:
            cap.release()
        stop_encoder(encoder)
        log("Stopped.")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    main()
