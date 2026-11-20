"""Locate ffmpeg/ffprobe and keep video work optional.

The server is useful without ffmpeg (music, images, documents, direct video
playback).  When the binaries are present we additionally get video metadata,
thumbnails and HLS transcoding.  Resolution order:

1. ``MEDIA_FFMPEG`` / ``MEDIA_FFPROBE``
2. ``ffmpeg`` / ``ffprobe`` on PATH
3. the binary bundled by the optional ``imageio-ffmpeg`` package
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FfmpegTools:
    ffmpeg: str | None
    ffprobe: str | None

    @property
    def can_thumbnail(self) -> bool:
        return self.ffmpeg is not None

    @property
    def can_transcode(self) -> bool:
        return self.ffmpeg is not None

    @property
    def can_probe(self) -> bool:
        return self.ffprobe is not None or self.ffmpeg is not None


_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], FfmpegTools] = {}


def _imageio_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def discover(ffmpeg: str = "", ffprobe: str = "") -> FfmpegTools:
    key = (ffmpeg or "", ffprobe or "")
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        resolved_ffmpeg = None
        if ffmpeg:
            resolved_ffmpeg = ffmpeg if Path(ffmpeg).exists() else shutil.which(ffmpeg)
        resolved_ffmpeg = resolved_ffmpeg or shutil.which("ffmpeg") or _imageio_ffmpeg()

        resolved_ffprobe = None
        if ffprobe:
            resolved_ffprobe = ffprobe if Path(ffprobe).exists() else shutil.which(ffprobe)
        resolved_ffprobe = resolved_ffprobe or shutil.which("ffprobe")

        tools = FfmpegTools(ffmpeg=resolved_ffmpeg, ffprobe=resolved_ffprobe)
        _CACHE[key] = tools
        return tools


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
