"""Media metadata probing (mutagen first, ffmpeg/Pillow as fallbacks).

Everything here is best-effort: an unreadable file must never abort a scan.
The scanner stores the resulting dict in SQLite and only re-probes when the
file's size or mtime changes.
"""

from __future__ import annotations

import base64
import io
import json
import re
import struct
from pathlib import Path

from .ffmpeg import FfmpegTools, run

COVER_NAMES = (
    "cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg",
    "folder.png", "Cover.jpg", "Folder.jpg", "front.jpg", "albumart.jpg",
)

_LEADING_NUMBER = re.compile(r"^\s*(?:\d{1,3}[\s._-]+)+")
_AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg", ".opus", ".m4b", ".aiff", ".wma"}


def display_title(stem: str) -> str:
    """Turn ``01 - Song`` into ``Song`` without touching real punctuation."""
    cleaned = _LEADING_NUMBER.sub("", stem).strip()
    return cleaned or stem


def sidecar_cover(path: Path) -> Path | None:
    for name in COVER_NAMES:
        candidate = path.parent / name
        if candidate.is_file():
            return candidate
    for candidate in (path.with_suffix(".jpg"), path.with_suffix(".png"),
                      path.with_suffix(".jpeg"), path.with_suffix(".webp")):
        if candidate.is_file():
            return candidate
    return None


def sidecar_lyrics(path: Path) -> Path | None:
    candidate = path.with_suffix(".lrc")
    return candidate if candidate.is_file() else None


def _normalise_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _first_text(tags, *keys: str) -> str:
    if tags is None:
        return ""
    for key in keys:
        try:
            value = tags.get(key)
        except Exception:
            continue
        if not value:
            continue
        if isinstance(value, (list, tuple)):
            value = value[0]
        text = _normalise_title(str(value))
        if text:
            return text
    return ""


def _audio_metadata(path: Path, tools: FfmpegTools) -> dict:
    result: dict = {"has_cover": False, "has_lyrics": False}
    tags = None
    raw = None
    try:
        import mutagen

        raw = mutagen.File(path)
        if raw is None:
            raise ValueError("unsupported audio container")
        tags = raw.tags
        info = raw.info
        result["duration"] = round(float(getattr(info, "length", 0) or 0), 3) or None
        bitrate = getattr(info, "bitrate", None)
        if bitrate:
            result["bitrate"] = int(bitrate)
        sample_rate = getattr(info, "sample_rate", None)
        if sample_rate:
            result["sample_rate"] = int(sample_rate)
    except Exception:
        raw = None

    if raw is not None:
        result["title"] = _first_text(tags, "title", "TIT2", "©nam", "Title")
        result["artist"] = _first_text(tags, "artist", "TPE1", "©ART", "Artist")
        result["album"] = _first_text(tags, "album", "TALB", "©alb", "Album")
        result["albumartist"] = _first_text(tags, "albumartist", "TPE2", "aART", "AlbumArtist")
        result["genre"] = _first_text(tags, "genre", "TCON", "©gen", "Genre")
        result["year"] = _first_text(tags, "date", "TDRC", "©day", "Date")
        result["track"] = _first_text(tags, "tracknumber", "TRCK", "trkn", "Track")
        embedded = _embedded_cover(raw)
        result["has_cover"] = embedded is not None
        lyrics = _embedded_lyrics(raw)
        if lyrics:
            result["has_lyrics"] = True
            result["lyrics_text"] = lyrics

    if not result.get("duration") and tools.can_probe:
        probed = probe_with_ffmpeg(path, tools)
        if probed:
            result["duration"] = probed.get("duration")
    return result


def _embedded_cover(raw) -> bytes | None:
    """Return embedded artwork bytes for ID3/FLAC/MP4/Vorbis containers."""
    try:
        pictures = getattr(raw, "pictures", None)
        if pictures:
            data = pictures[0].data
            return data or None
    except Exception:
        pass
    tags = getattr(raw, "tags", None)
    if tags is None:
        return None
    try:
        frames = tags.getall("APIC")
        if frames:
            return frames[0].data
    except Exception:
        pass
    try:
        cover = tags.get("covr")
        if cover:
            return bytes(cover[0])
    except Exception:
        pass
    try:
        blocks = tags.get("metadata_block_picture")
        if blocks:
            from mutagen.flac import Picture

            return Picture(base64.b64decode(blocks[0])).data
    except Exception:
        pass
    return None


def _embedded_lyrics(raw) -> str:
    tags = getattr(raw, "tags", None)
    if tags is None:
        return ""
    try:
        frames = tags.getall("USLT")
        if frames:
            return str(frames[0].text).strip()
    except Exception:
        pass
    try:
        for frame in tags.getall("TXXX"):
            desc = str(getattr(frame, "desc", "")).lower()
            if desc in {"lyrics", "lyric", "unsyncedlyrics", "unsynced lyrics"}:
                return str(frame.text[0]).strip()
    except Exception:
        pass
    for key in ("lyrics", "unsyncedlyrics", "©lyr", "LYRICS"):
        try:
            value = tags.get(key)
        except Exception:
            continue
        if value:
            if isinstance(value, (list, tuple)):
                value = value[0]
            return str(value).strip()
    return ""


def _image_metadata(path: Path) -> dict:
    result: dict = {}
    try:
        from PIL import Image

        with Image.open(path) as image:
            result["width"], result["height"] = image.size
            result["meta"] = {"format": image.format, "mode": image.mode}
    except Exception:
        pass
    return result


def _video_metadata(path: Path, tools: FfmpegTools) -> dict:
    result: dict = {}
    probed = probe_with_ffmpeg(path, tools)
    if probed:
        result.update(probed)
    if not result.get("duration"):
        parsed = _mp4_metadata(path)
        if parsed:
            result.update(parsed)
    result["has_cover"] = False  # thumbnail is generated on demand
    return result


def probe_with_ffmpeg(path: Path, tools: FfmpegTools) -> dict | None:
    """Use ffprobe when available; otherwise parse ``ffmpeg -i`` output."""
    if tools.ffprobe:
        try:
            proc = run([tools.ffprobe, "-v", "error", "-show_streams", "-show_format",
                        "-of", "json", str(path)], timeout=20)
            if proc.returncode == 0 and proc.stdout:
                data = json.loads(proc.stdout.decode("utf-8", "replace"))
                return _from_ffprobe(data)
        except Exception:
            pass
    if tools.ffmpeg:
        try:
            proc = run([tools.ffmpeg, "-hide_banner", "-i", str(path)], timeout=20)
            text = proc.stderr.decode("utf-8", "replace")
            return _from_ffmpeg_text(text)
        except Exception:
            pass
    return None


def _from_ffprobe(data: dict) -> dict:
    result: dict = {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    duration = fmt.get("duration") or (video or {}).get("duration")
    if duration:
        try:
            result["duration"] = round(float(duration), 3)
        except (TypeError, ValueError):
            pass
    if fmt.get("bit_rate"):
        try:
            result["bitrate"] = int(fmt["bit_rate"])
        except (TypeError, ValueError):
            pass
    if video:
        result["width"] = video.get("width")
        result["height"] = video.get("height")
        result["video_codec"] = video.get("codec_name")
        if video.get("duration") and not result.get("duration"):
            try:
                result["duration"] = round(float(video["duration"]), 3)
            except (TypeError, ValueError):
                pass
    if audio:
        result["audio_codec"] = audio.get("codec_name")
        result["sample_rate"] = audio.get("sample_rate")
        result["channels"] = audio.get("channels")
    result["meta"] = {"format": fmt.get("format_name")}
    return result


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Stream #\S+.*?: Video: ([^,]+).*?(\d{2,5})x(\d{2,5})")
_AUDIO_RE = re.compile(r"Stream #\S+.*?: Audio: ([^,]+)")


def _from_ffmpeg_text(text: str) -> dict | None:
    result: dict = {}
    match = _DURATION_RE.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        result["duration"] = round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)
    video = _VIDEO_RE.search(text)
    if video:
        result["video_codec"] = video.group(1).strip()
        result["width"] = int(video.group(2))
        result["height"] = int(video.group(3))
    audio = _AUDIO_RE.search(text)
    if audio:
        result["audio_codec"] = audio.group(1).strip()
    return result or None


def _mp4_metadata(path: Path) -> dict | None:
    """Minimal ISO-BMFF parser: duration from mvhd, dimensions from tkhd."""
    try:
        with path.open("rb") as handle:
            data = handle.read(4 * 1024 * 1024)
    except OSError:
        return None

    duration: float | None = None
    width: int | None = None
    height: int | None = None

    def walk(buf: bytes, offset: int, end: int) -> None:
        nonlocal duration, width, height
        while offset + 8 <= end:
            size = struct.unpack(">I", buf[offset:offset + 4])[0]
            kind = buf[offset + 4:offset + 8]
            header = 8
            if size == 1 and offset + 16 <= end:
                size = struct.unpack(">Q", buf[offset + 8:offset + 16])[0]
                header = 16
            if size < header or offset + size > end:
                return
            if kind in {b"moov", b"trak", b"mdia"}:
                walk(buf, offset + header, offset + size)
            elif kind == b"mvhd":
                version = buf[offset + header]
                if version == 0 and offset + header + 20 <= end:
                    timescale = struct.unpack(">I", buf[offset + header + 12:offset + header + 16])[0]
                    units = struct.unpack(">I", buf[offset + header + 16:offset + header + 20])[0]
                else:
                    timescale = struct.unpack(">I", buf[offset + header + 20:offset + header + 24])[0]
                    units = struct.unpack(">Q", buf[offset + header + 24:offset + header + 32])[0]
                if timescale:
                    duration = round(units / timescale, 3)
            elif kind == b"tkhd":
                version = buf[offset + header]
                base = offset + header + (76 if version == 0 else 92)
                if base + 8 <= end:
                    width = struct.unpack(">I", buf[base:base + 4])[0] >> 16
                    height = struct.unpack(">I", buf[base + 4:base + 8])[0] >> 16
            offset += size

    walk(data, 0, len(data))
    if duration is None and width is None:
        return None
    result: dict = {}
    if duration:
        result["duration"] = duration
    if width and height:
        result["width"], result["height"] = width, height
    return result


def probe(path: Path, kind: str, tools: FfmpegTools) -> dict:
    """Probe a single file and return a JSON-serialisable metadata dict."""
    result: dict = {
        "title": display_title(path.stem),
        "artist": "",
        "album": "",
        "albumartist": "",
        "duration": None,
        "width": None,
        "height": None,
        "video_codec": None,
        "audio_codec": None,
        "bitrate": None,
        "sample_rate": None,
        "has_cover": False,
        "has_lyrics": False,
        "cover_path": None,
        "lyrics_path": None,
    }
    if kind == "audio":
        result.update(_audio_metadata(path, tools))
    elif kind == "video":
        result.update(_video_metadata(path, tools))
    elif kind == "image":
        result.update(_image_metadata(path))

    cover = sidecar_cover(path)
    if cover is not None:
        result["cover_path"] = cover
        result["has_cover"] = True
    lyrics = sidecar_lyrics(path)
    if lyrics is not None:
        result["lyrics_path"] = lyrics
        result["has_lyrics"] = True
    return result


def extract_cover(path: Path, kind: str, out_path: Path, tools: FfmpegTools) -> bool:
    """Write a JPEG cover to ``out_path``. Returns True when something was written."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp = out_path.with_suffix(".tmp.jpg")
    data: bytes | None = None
    if kind in ("audio", "video"):
        try:
            import mutagen

            raw = mutagen.File(path)
            if raw is not None:
                data = _embedded_cover(raw)
        except Exception:
            data = None
    if data is None and tools.can_thumbnail:
        try:
            proc = run([tools.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", "1", "-i", str(path), "-frames:v", "1",
                        "-vf", "scale=800:-2", "-q:v", "3", str(temp)], timeout=30)
            if proc.returncode == 0 and temp.is_file() and temp.stat().st_size > 0:
                data = temp.read_bytes()
        except Exception:
            data = None
    if data is None:
        temp.unlink(missing_ok=True)
        return False
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((1000, 1000))
            image.save(temp, "JPEG", quality=86, optimize=True)
    except Exception:
        temp.write_bytes(data)
    try:
        temp.replace(out_path)
        return True
    except OSError:
        temp.unlink(missing_ok=True)
        return False


def extract_audio_cover_to_share(path: Path, out_path: Path, tools: FfmpegTools) -> bool:
    """Cover extraction used by the share endpoint (audio only, embedded first)."""
    return extract_cover(path, "audio", out_path, tools)
