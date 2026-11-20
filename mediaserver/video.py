"""On-demand HLS transcoding for browser playback.

Browsers can play H.264/AAC MP4 directly with byte ranges, so most well-encoded
files never touch ffmpeg.  Anything else (HEVC, MKV, exotic audio) can be
transcoded into a temporary HLS rendition on request; the web player polls the
job and then attaches the playlist.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Config
from .library import LibraryManager

log = logging.getLogger("mediaserver.video")

PROFILE_RE = re.compile(r"^[0-9]{3,4}p$")
JOB_RE = re.compile(r"^[a-f0-9]{20}-[0-9]{3,4}p$")
SEGMENT_RE = re.compile(r"^seg[0-9]{5}\.ts$")

DIRECT_EXTS = {".mp4", ".m4v", ".webm"}
DIRECT_VIDEO_CODECS = {"h264", "avc1", "vp8", "vp9", "av1"}
DIRECT_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis", "none", None, ""}


class VideoService:
    def __init__(self, config: Config, library: LibraryManager):
        self.config = config
        self.library = library
        self.tools = library.tools
        self.enabled = bool(config.hls_enabled and self.tools.can_transcode)
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=config.hls_concurrency,
                                        thread_name_prefix="hls") if self.enabled else None

    # ── public helpers ──────────────────────────────────────────────
    @property
    def hls_dir(self) -> Path:
        return self.config.hls_cache_dir

    def direct_playable(self, row) -> bool:
        ext = Path(row["rel"]).suffix.lower()
        video_codec = (row["video_codec"] or "").lower()
        audio_codec = (row["audio_codec"] or "").lower()
        return (ext in DIRECT_EXTS
                and (not video_codec or video_codec in DIRECT_VIDEO_CODECS)
                and (not audio_codec or audio_codec in DIRECT_AUDIO_CODECS))

    def profile_name(self, row) -> str:
        source_height = row["height"] or 0
        target = self.config.hls_height
        if source_height and source_height < target:
            target = source_height
        target = max(144, min(target, 2160))
        if target % 2:
            target += 1
        return f"{target}p"

    def job_id(self, row, profile: str | None = None) -> str:
        return f"{row['id']}-{profile or self.profile_name(row)}"

    def _job_dir(self, job_id: str) -> Path:
        return self.hls_dir / job_id

    def ready(self, job_id: str) -> bool:
        directory = self._job_dir(job_id)
        return (directory / "done").is_file() and (directory / "index.m3u8").is_file()

    def status(self, row, profile: str | None = None) -> dict:
        job_id = self.job_id(row, profile)
        if self.ready(job_id):
            return {"jobId": job_id, "status": "ready", "progress": 1.0,
                    "playlist": f"/api/v1/hls/{job_id}/index.m3u8"}
        with self._lock:
            job = self._jobs.get(job_id)
        if job:
            return dict(job, playlist=None)
        return {"jobId": job_id, "status": "idle", "progress": 0.0, "playlist": None}

    def start(self, row, profile: str | None = None) -> dict:
        if not self.enabled:
            raise RuntimeError("HLS 转码未启用(缺少 ffmpeg 或 MEDIA_HLS_ENABLED=0)")
        if row["kind"] != "video":
            raise ValueError("只有视频需要转码")
        job_id = self.job_id(row, profile)
        if self.ready(job_id):
            return {"jobId": job_id, "status": "ready", "progress": 1.0,
                    "playlist": f"/api/v1/hls/{job_id}/index.m3u8"}
        with self._lock:
            existing = self._jobs.get(job_id)
            if existing and existing["status"] in ("queued", "running"):
                return dict(existing, playlist=None)
            source = self.library.resolve(row)
            if source is None:
                raise ValueError("源文件不存在")
            job = {
                "jobId": job_id,
                "itemId": row["id"],
                "profile": job_id.split("-")[-1],
                "status": "queued",
                "progress": 0.0,
                "error": None,
                "startedAt": time.time(),
                "playlist": None,
            }
            self._jobs[job_id] = job
        self._evict()
        self._pool.submit(self._run, job, source, row)  # type: ignore[union-attr]
        return dict(job)

    def playlist(self, job_id: str) -> str | None:
        if not JOB_RE.fullmatch(job_id):
            return None
        directory = self._job_dir(job_id)
        playlist = directory / "index.m3u8"
        if not self.ready(job_id):
            return None
        try:
            text = playlist.read_text("utf-8")
        except OSError:
            return None
        # Segments are relative, so the browser requests
        # /api/v1/hls/<job>/seg00000.ts which we serve below.
        return text

    def segment(self, job_id: str, name: str) -> Path | None:
        if not JOB_RE.fullmatch(job_id) or not SEGMENT_RE.fullmatch(name):
            return None
        directory = self._job_dir(job_id)
        candidate = (directory / name).resolve()
        try:
            candidate.relative_to(directory.resolve())
        except ValueError:
            return None
        if candidate.is_file():
            return candidate
        return None

    # ── worker ──────────────────────────────────────────────────────
    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(fields)

    def _run(self, job: dict, source: Path, row) -> None:
        job_id = job["jobId"]
        directory = self._job_dir(job_id)
        staging = directory.with_name(directory.name + ".part")
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(directory, ignore_errors=True)
            height = int(job_id.rsplit("-", 1)[-1].rstrip("p"))
            duration = float(row["duration"] or 0)
            command = [
                self.tools.ffmpeg or "ffmpeg",
                "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-i", str(source),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", f"scale=-2:{height}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-profile:v", "main", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                "-f", "hls",
                "-hls_time", str(self.config.hls_segment),
                "-hls_playlist_type", "vod",
                "-hls_flags", "independent_segments",
                "-hls_segment_filename", str(staging / "seg%05d.ts"),
                "-progress", "pipe:1", "-nostats",
                str(staging / "index.m3u8"),
            ]
            self._update(job_id, status="running", startedAt=time.time())
            import subprocess

            proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms=") and duration > 0:
                    try:
                        seconds = int(line.split("=", 1)[1]) / 1_000_000
                        progress = max(0.0, min(0.99, seconds / duration))
                        self._update(job_id, progress=round(progress, 3))
                    except ValueError:
                        continue
            stderr = proc.stderr.read() if proc.stderr else ""
            code = proc.wait()
            if code != 0:
                raise RuntimeError((stderr or "ffmpeg failed").strip()[-500:])
            if not (staging / "index.m3u8").is_file():
                raise RuntimeError("ffmpeg 未生成播放列表")
            (staging / "done").write_text(str(time.time()), "utf-8")
            shutil.rmtree(directory, ignore_errors=True)
            os.replace(staging, directory)
            self._update(job_id, status="ready", progress=1.0,
                         finishedAt=time.time(),
                         playlist=f"/api/v1/hls/{job_id}/index.m3u8")
        except Exception as exc:
            log.warning("transcode %s failed: %s", job_id, exc)
            self._update(job_id, status="error", error=str(exc)[:500], finishedAt=time.time())
            shutil.rmtree(staging, ignore_errors=True)

    # ── cache housekeeping ─────────────────────────────────────────
    def _evict(self) -> None:
        try:
            entries = [p for p in self.hls_dir.iterdir() if p.is_dir()]
        except OSError:
            return
        total = 0
        for path in entries:
            for file in path.rglob("*"):
                if file.is_file():
                    try:
                        total += file.stat().st_size
                    except OSError:
                        pass
        limit = self.config.hls_cache_mb * 1024 * 1024
        if total <= limit:
            return
        entries.sort(key=lambda p: p.stat().st_mtime)
        with self._lock:
            active = {job["jobId"] for job in self._jobs.values()
                      if job["status"] in ("queued", "running")}
        for path in entries:
            if total <= limit:
                break
            if path.name in active:
                continue
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            shutil.rmtree(path, ignore_errors=True)
            total -= size

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
