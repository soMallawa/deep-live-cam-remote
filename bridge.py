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


def start_encoder(rtsp_url, width, height, fps):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "h264_nvenc",
        "-preset",
        "p1",
        "-tune",
        "ll",
        "-rc",
        "cbr",
        "-b:v",
        BITRATE,
        "-maxrate",
        BITRATE,
        "-bufsize",
        BITRATE,
        "-g",
        str(fps),
        "-bf",
        "0",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
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


def get_stream_dimensions(cap):
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width <= 0 or height <= 0:
        return WIDTH, HEIGHT
    return width, height


def process_stream(cap, encoder, out_width, out_height):
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
            encoder = start_encoder(RTSP_OUT, out_width, out_height, FPS)
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

            in_width, in_height = get_stream_dimensions(cap)
            out_width = in_width if in_width > 0 else WIDTH
            out_height = in_height if in_height > 0 else HEIGHT
            log(f"Input stream: {in_width}x{in_height}; output stream: {out_width}x{out_height}@{FPS}")

            if encoder is None or encoder.poll() is not None:
                log(f"Starting RTSP output encoder: {RTSP_OUT}")
                encoder = start_encoder(RTSP_OUT, out_width, out_height, FPS)
                if encoder is None:
                    sys.exit(1)

            keep_encoder, encoder = process_stream(cap, encoder, out_width, out_height)
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
