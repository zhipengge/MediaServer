from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from mediaserver import create_app
from mediaserver.ffmpeg import discover
from tests.conftest import make_config


def _make_video(path: Path) -> bool:
    tools = discover("", "")
    if not tools.ffmpeg:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        tools.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ]
    proc = subprocess.run(command, capture_output=True)
    return proc.returncode == 0 and path.is_file()


def test_transcode_roundtrip(tmp_path):
    video = tmp_path / "media" / "videos" / "clip.mp4"
    if not _make_video(video):
        pytest.skip("ffmpeg with libx264 not available")
    config = make_config(tmp_path, hls_enabled=True)
    app = create_app(config, start_scanner=False)
    client = app.test_client()
    app.extensions["mediaserver"].library.scan()

    items = client.get("/api/v1/items?kind=video").get_json()
    assert items["total"] == 1
    item = items["items"][0]
    assert item["width"] == 320
    assert item["duration"] and item["duration"] >= 1.5
    assert item["thumb"].startswith("/api/v1/thumb/")

    response = client.post(f"/api/v1/transcode/{item['id']}", json={})
    assert response.status_code in (200, 202)
    job_id = response.get_json()["jobId"]

    deadline = time.time() + 90
    state = {}
    while time.time() < deadline:
        state = client.get(f"/api/v1/jobs/{job_id}").get_json()
        if state["status"] in ("ready", "error"):
            break
        time.sleep(0.5)
    assert state["status"] == "ready", state

    playlist = client.get(f"/api/v1/hls/{job_id}/index.m3u8")
    assert playlist.status_code == 200
    text = playlist.data.decode("utf-8")
    assert "#EXTM3U" in text and "#EXTINF" in text
    segments = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert segments
    segment = client.get(f"/api/v1/hls/{job_id}/{segments[0]}")
    assert segment.status_code == 200
    assert segment.headers["Content-Type"] == "video/mp2t"
    assert segment.data[:1] == b"\x47"

    thumb = client.get(f"/api/v1/thumb/{item['id']}?w=320")
    assert thumb.status_code == 200
    assert thumb.headers["Content-Type"] == "image/jpeg"
