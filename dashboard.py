#!/usr/bin/env python3
"""
Deep-Live-Cam Remote — Web Dashboard
Manages the bridge subprocess, streams logs via SSE, proxies MediaMTX status.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, send_file, send_from_directory

# ---------- config ----------
HOST = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
PORT = int(os.environ.get("DASHBOARD_PORT", 8080))
SOURCE_IMAGE = os.environ.get("SOURCE_IMAGE", "/app/source.jpg")
BRIDGE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.py")
MEDIAMTX_API = os.environ.get("MEDIAMTX_API", "http://127.0.0.1:9997")
RTSP_IN = os.environ.get("RTSP_IN", "rtsp://127.0.0.1:8554/cam_in")
RTSP_OUT = os.environ.get("RTSP_OUT", "rtsp://127.0.0.1:8554/cam_out")
LOG_HISTORY = 500
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# ---------- state ----------
_bridge_proc = None
_bridge_lock = threading.Lock()
_log_buffer = []
_log_subs = []
_log_lock = threading.Lock()

app = Flask(__name__, static_folder=None)


# ---------- logging ----------
def _add_log(line):
    with _log_lock:
        _log_buffer.append(line)
        if len(_log_buffer) > LOG_HISTORY:
            _log_buffer.pop(0)
        for q in _log_subs:
            q.put(line)


def _drain_stream(stream):
    try:
        for raw in stream:
            _add_log(raw.rstrip("\n"))
    except Exception:
        pass


# ---------- bridge management ----------
def _is_running():
    with _bridge_lock:
        return _bridge_proc is not None and _bridge_proc.poll() is None


def _start_bridge():
    with _bridge_lock:
        global _bridge_proc
        if _bridge_proc is not None and _bridge_proc.poll() is None:
            return False, "Bridge is already running."

        env = {**os.environ, "SOURCE_IMAGE": SOURCE_IMAGE}
        try:
            proc = subprocess.Popen(
                [sys.executable, BRIDGE_SCRIPT],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
        except Exception as exc:
            return False, f"Failed to start bridge: {exc}"

        _bridge_proc = proc

    _add_log("[dashboard] Bridge started.")
    threading.Thread(target=_drain_stream, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=_watch_bridge, args=(proc,), daemon=True).start()
    return True, "Bridge started."


def _stop_bridge():
    with _bridge_lock:
        global _bridge_proc
        proc = _bridge_proc
        if proc is None or proc.poll() is not None:
            return False, "Bridge is not running."
        proc.terminate()
        _bridge_proc = None

    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
    _add_log("[dashboard] Bridge stopped.")
    return True, "Bridge stopped."


def _watch_bridge(proc):
    proc.wait()
    _add_log(f"[dashboard] Bridge exited with code {proc.returncode}.")


# ---------- MediaMTX helpers ----------
def _mtx_path_info(path_name):
    try:
        r = requests.get(f"{MEDIAMTX_API}/v3/paths/get/{path_name}", timeout=2)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def _extract_path_summary(info):
    if not info:
        return {"ready": False}
    src = info.get("source") or {}
    return {
        "ready": info.get("ready", False),
        "readers": len(info.get("readers") or []),
        "source_type": src.get("type", ""),
    }


# ---------- routes ----------
@app.route("/")
def index():
    return send_from_directory(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"),
        "dashboard.html",
    )


@app.route("/api/status")
def api_status():
    host = request.host.split(":")[0]
    cam_in_info = _mtx_path_info("cam_in")
    cam_out_info = _mtx_path_info("cam_out")
    return jsonify(
        {
            "bridge_running": _is_running(),
            "source_image_exists": os.path.exists(SOURCE_IMAGE),
            "paths": {
                "cam_in": _extract_path_summary(cam_in_info),
                "cam_out": _extract_path_summary(cam_out_info),
            },
            "urls": {
                "whip": f"http://{host}:8889/cam_in/whip",
                "whep": f"http://{host}:8889/cam_out/whep",
                "rtsp_in": RTSP_IN,
                "rtsp_out": RTSP_OUT,
            },
        }
    )


@app.route("/api/start", methods=["POST"])
def api_start():
    ok, msg = _start_bridge()
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)


@app.route("/api/stop", methods=["POST"])
def api_stop():
    ok, msg = _stop_bridge()
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "message": "No file field in request."}), 400

    f = request.files["file"]
    ext = Path(f.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        return jsonify({"ok": False, "message": f"Unsupported type {ext}. Use jpg/png/webp."}), 400

    try:
        parent = os.path.dirname(SOURCE_IMAGE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        f.save(SOURCE_IMAGE)
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Save failed: {exc}"}), 500

    _add_log(f"[dashboard] Source image updated: {SOURCE_IMAGE}")
    return jsonify({"ok": True, "message": "Source image uploaded."})


@app.route("/api/source-image")
def api_source_image():
    if not os.path.exists(SOURCE_IMAGE):
        return "", 404
    return send_file(SOURCE_IMAGE)


@app.route("/api/logs")
def api_logs():
    sub_q = queue.SimpleQueue()

    def generate():
        with _log_lock:
            history = list(_log_buffer)
            _log_subs.append(sub_q)
        try:
            for line in history:
                yield f"data: {json.dumps(line)}\n\n"
            while True:
                try:
                    line = sub_q.get(timeout=15)
                    yield f"data: {json.dumps(line)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _log_lock:
                try:
                    _log_subs.remove(sub_q)
                except ValueError:
                    pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- main ----------
if __name__ == "__main__":
    _add_log("[dashboard] Dashboard starting on port " + str(PORT))
    app.run(host=HOST, port=PORT, threaded=True, debug=False)
