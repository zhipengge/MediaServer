"""Small shared helpers: media classification, path safety, URLs, LRC parsing."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import mimetypes
import os
import re
import socket
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg", ".opus", ".m4b", ".aiff", ".wma"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".ts", ".flv", ".wmv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".heic", ".avif"}
DOC_EXTS = {".pdf", ".epub", ".txt", ".md", ".csv", ".json", ".lrc", ".srt", ".ass", ".vtt", ".m3u", ".m3u8"}

KIND_BY_EXT: dict[str, str] = {}
for _ext in AUDIO_EXTS:
    KIND_BY_EXT[_ext] = "audio"
for _ext in VIDEO_EXTS:
    KIND_BY_EXT[_ext] = "video"
for _ext in IMAGE_EXTS:
    KIND_BY_EXT[_ext] = "image"
for _ext in DOC_EXTS:
    KIND_BY_EXT[_ext] = "doc"

KIND_LABELS = {"audio": "音乐", "video": "视频", "image": "图片", "doc": "文档"}

MIME_BY_EXT = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac", ".wav": "audio/wav",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".m4b": "audio/mp4",
    ".aiff": "audio/aiff", ".wma": "audio/x-ms-wma",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".webm": "video/webm", ".avi": "video/x-msvideo",
    ".ts": "video/mp2t", ".flv": "video/x-flv", ".wmv": "video/x-ms-wmv",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp", ".tiff": "image/tiff", ".heic": "image/heic",
    ".avif": "image/avif",
    ".pdf": "application/pdf", ".epub": "application/epub+zip",
    ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8", ".json": "application/json",
    ".lrc": "text/plain; charset=utf-8", ".srt": "text/plain; charset=utf-8",
    ".ass": "text/plain; charset=utf-8", ".vtt": "text/vtt; charset=utf-8",
    ".m3u": "audio/x-mpegurl", ".m3u8": "application/vnd.apple.mpegurl",
}

_DANGEROUS = re.compile(r"[\x00-\x1f\x7f]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def kind_for(path: Path) -> str | None:
    return KIND_BY_EXT.get(path.suffix.lower())


def mime_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MIME_BY_EXT:
        return MIME_BY_EXT[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts if part not in ("/", "."))


_INITIAL_CACHE: dict[str, str] = {}


def artist_id(name: str) -> str:
    """Stable id for an artist name (URL-safe, independent of ordering)."""
    return hashlib.sha1(("artist:" + (name or "")).encode("utf-8", "surrogateescape")).hexdigest()[:16]


def artist_initial(name: str) -> str:
    """Pinyin/latin first letter bucket for the A-Z artist index."""
    name = (name or "").strip()
    if not name:
        return "#"
    cached = _INITIAL_CACHE.get(name)
    if cached is not None:
        return cached
    first = name[0]
    if first.isascii() and first.isalpha():
        initial = first.upper()
    elif first.isdigit():
        initial = "#"
    else:
        initial = "#"
        try:
            from pypinyin import Style, lazy_pinyin

            letters = lazy_pinyin(name, style=Style.FIRST_LETTER, errors="default")
            if letters and letters[0] and letters[0][0].isalpha():
                initial = letters[0][0].upper()
        except Exception:
            pass
    _INITIAL_CACHE[name] = initial
    return initial


def item_id(rel: str) -> str:
    """Stable, URL-safe id for a mount-relative path."""
    return hashlib.sha1(rel.encode("utf-8", "surrogateescape")).hexdigest()[:20]


def rel_to_url_path(rel: str) -> str:
    return "/media/" + quote(rel, safe="/")


def media_url(rel: str, base: str = "") -> str:
    return (base.rstrip("/") + rel_to_url_path(rel)) if base else rel_to_url_path(rel)


def safe_rel(rel: str) -> str | None:
    """Validate a mount-relative URL path. Returns a normalised rel or None.

    Rejects traversal, absolute paths, NUL/control bytes and Windows separators.
    URL decoding happens before this call (Flask already decodes %2e%2e).
    """
    if not isinstance(rel, str):
        return None
    rel = unquote(rel.strip())
    if not rel or rel == "/" or "\\" in rel or _DANGEROUS.search(rel):
        return None
    rel = rel.lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    if any(part.startswith(".") for part in parts):
        # Hidden files are never part of the library; refusing them keeps
        # backups, editor swap files and .git out of the media surface.
        return None
    return "/".join(parts)


def resolve_rel(mounts: dict[str, Path], rel: str) -> Path | None:
    """Map a validated rel to a real path, enforcing mount containment."""
    clean = safe_rel(rel)
    if clean is None:
        return None
    mount, _, rest = clean.partition("/")
    root = mounts.get(mount)
    if root is None or not rest:
        return None
    try:
        root_real = root.resolve()
        candidate = (root_real / rest).resolve()
        candidate.relative_to(root_real)
    except (OSError, ValueError):
        return None
    if not candidate.is_file():
        return None
    return candidate


def rel_for_path(mounts: dict[str, Path], path: Path) -> str | None:
    """Inverse of :func:`resolve_rel`; used by the scanner."""
    try:
        real = path.resolve()
    except OSError:
        return None
    best: tuple[str, Path] | None = None
    for name, root in mounts.items():
        try:
            root_real = root.resolve()
        except OSError:
            continue
        if real.is_relative_to(root_real) and (
                best is None or len(str(root_real)) > len(str(best[1]))):
            best = (name, root_real)
    if best is None:
        return None
    name, root_real = best
    return name + "/" + real.relative_to(root_real).as_posix()


def human_size(num: int | float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def escape(value: object) -> str:
    return html.escape("" if value is None else str(value))


def now() -> float:
    return time.time()


def duration_label(seconds: float | int | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    total = round(float(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


_TS = re.compile(r"^\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(text: str) -> dict:
    """Parse an LRC file into metadata + timed lines (offset applied)."""
    offset_ms = 0
    meta: dict[str, str] = {}
    lines: list[dict] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        tag = re.match(r"^\[(ti|ar|al|by|offset|length|tool):([^\]]*)\]$", raw, re.I)
        if tag:
            key, value = tag.group(1).lower(), tag.group(2).strip()
            meta[key] = value
            if key == "offset":
                try:
                    offset_ms = int(value)
                except ValueError:
                    offset_ms = 0
            continue
        stamps = []
        rest = raw
        while True:
            match = _TS.match(rest)
            if not match:
                break
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            fraction = match.group(3) or "0"
            if len(fraction) == 1:
                millis = int(fraction) * 100
            elif len(fraction) == 2:
                millis = int(fraction) * 10
            else:
                millis = int(fraction[:3])
            stamps.append(minutes * 60 + seconds + millis / 1000)
            rest = rest[match.end():]
        content = rest.strip()
        if not stamps:
            if content:
                lines.append({"time": None, "text": content})
            continue
        if not content:
            continue
        for stamp in stamps:
            lines.append({"time": round(max(0.0, stamp + offset_ms / 1000), 3), "text": content})
    timed = [line for line in lines if line["time"] is not None]
    timed.sort(key=lambda line: line["time"])
    return {
        "metadata": meta,
        "lines": timed,
        "plain": [line["text"] for line in lines],
        "text": "\n".join(line["text"] for line in timed).strip(),
        "synced": bool(timed),
    }


def host_is_private(host: str | None) -> bool:
    """True when a hostname resolves to a private/loopback/link-local address."""
    host = (host or "").strip("[]").lower()
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        address = info[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


_ID_PART = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$")


def same_origin_path(raw: str, request_host: str, mounts: dict[str, Path]) -> str | None:
    """Normalise a share URL to a same-origin relative path."""
    if not raw or not isinstance(raw, str):
        return None
    parsed = urlparse(raw.strip())
    if parsed.scheme:
        if parsed.netloc.lower() != (request_host or "").lower():
            return None
    elif not raw.startswith("/"):
        return None
    path = unquote(parsed.path)
    if path.startswith("/media/"):
        rel = safe_rel(path[len("/media/"):])
        if rel is None or resolve_rel(mounts, rel) is None:
            return None
        return rel_to_url_path(rel)
    if path.startswith("/relay/url/"):
        parts = path[len("/relay/url/"):].split("/")
        if len(parts) == 2 and all(_ID_PART.fullmatch(p) for p in parts):
            return path
    return None


def sanitize_filename(name: str, fallback: str = "upload") -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = _CONTROL.sub("", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.strip(". ")
    if not name:
        return fallback
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    stem = stem[:180].strip() or fallback
    return f"{stem}.{suffix.lower()}" if suffix else stem


def valid_host(value: str) -> bool:
    """Host header sanity check used when building absolute manifest URLs."""
    if not value or len(value) > 255 or _CONTROL.search(value):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._\-\[\]:]+", value))


def iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
