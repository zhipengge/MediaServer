"""Share endpoint compatible with the muYunShared/1 contract."""

from __future__ import annotations

import logging
import os
import secrets
import time
from pathlib import Path

from .config import Config
from .db import Database
from .ffmpeg import FfmpegTools
from .metadata import extract_cover
from .util import (
    mime_for,
    same_origin_path,
    sanitize_filename,
)

log = logging.getLogger("mediaserver.share")

SHARE_LIMIT = 500


class ShareError(ValueError):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


class ShareService:
    def __init__(self, config: Config, db: Database, tools: FfmpegTools):
        self.config = config
        self.db = db
        self.tools = tools

    def _new_id(self) -> str:
        stamp = time.strftime("%Y%m%d%H%M%S", time.localtime())
        return f"share-{stamp}-{secrets.token_hex(3)}"

    def _payload(self, entry: dict) -> dict:
        data = {
            "id": entry["id"],
            "kind": entry.get("kind", "file"),
            "title": entry.get("title") or "",
            "artist": entry.get("artist") or "",
            "duration": entry.get("duration"),
            "playUrl": entry["playUrl"],
            "from": entry.get("from") or "",
            "sharedAt": int(entry.get("sharedAt") or time.time()),
        }
        cover = entry.get("cover")
        if cover:
            data["cover"] = cover
        if entry.get("size"):
            data["size"] = entry["size"]
        return data

    def create_from_file(self, *, file_storage, fields: dict) -> dict:
        if file_storage is None or not file_storage.filename:
            raise ShareError("multipart 分享缺少 file 字段")
        original = sanitize_filename(file_storage.filename, "share")
        suffix = Path(original).suffix.lower()
        if not suffix or len(suffix) > 8:
            raise ShareError("无法识别的文件名后缀")
        share_id = self._new_id()
        destination = self.config.shared_dir / f"{share_id}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_suffix(destination.suffix + ".part")
        size = 0
        limit = self.config.share_max_mb * 1024 * 1024
        try:
            file_storage.save(temp)
            size = temp.stat().st_size
            if size <= 0:
                raise ShareError("空文件")
            if size > limit:
                raise ShareError(f"文件超过分享上限 {self.config.share_max_mb} MB", 413)
            os.replace(temp, destination)
        except ShareError:
            temp.unlink(missing_ok=True)
            raise
        except Exception as exc:
            temp.unlink(missing_ok=True)
            log.warning("share save failed: %s", exc)
            raise ShareError("文件保存失败", 500) from exc

        play_url = "/media/shared/" + destination.name
        cover_url = self._extract_cover(destination, share_id, suffix)
        duration = _as_duration(fields.get("duration"))
        entry = {
            "id": share_id,
            "kind": "file",
            "title": (fields.get("title") or Path(original).stem)[:200],
            "artist": (fields.get("artist") or "")[:200],
            "duration": duration,
            "playUrl": play_url,
            "cover": cover_url,
            "from": (fields.get("from") or "")[:80],
            "sharedAt": time.time(),
            "size": size,
            "mime": mime_for(destination),
        }
        self._store(entry)
        return self._payload(entry)

    def create_from_url(self, payload: dict, request_host: str) -> dict:
        raw_url = payload.get("url")
        if not raw_url:
            raise ShareError("JSON 分享缺少 url")
        normalized = same_origin_path(str(raw_url), request_host, self.config.mounts)
        if normalized is None:
            raise ShareError("url 必须指向本服务器的 /media/ 或 /relay/url/ 路径")
        title = str(payload.get("title") or "")[:200]
        artist = str(payload.get("artist") or "")[:200]
        if not title:
            title = Path(normalized).stem
        entry = {
            "id": self._new_id(),
            "kind": "url",
            "title": title,
            "artist": artist,
            "duration": _as_duration(payload.get("duration")),
            "playUrl": normalized,
            "cover": None,
            "from": str(payload.get("from") or "")[:80],
            "sharedAt": time.time(),
            "size": None,
            "mime": None,
        }
        self._store(entry)
        return self._payload(entry)

    def _extract_cover(self, path: Path, share_id: str, suffix: str) -> str | None:
        if suffix not in {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus", ".m4b"}:
            return None
        cover_dir = self.config.shared_dir / "covers"
        cover_dir.mkdir(parents=True, exist_ok=True)
        out = cover_dir / f"{share_id}.jpg"
        if extract_cover(path, "audio", out, self.tools) and out.is_file():
            return "/media/shared/covers/" + out.name
        return None

    def _store(self, entry: dict) -> None:
        self.db.add_share(entry)
        # Trim the table so the share list cannot grow without bound.
        self.db.execute(
            "DELETE FROM shares WHERE id NOT IN "
            "(SELECT id FROM shares ORDER BY shared_at DESC LIMIT ?)", (SHARE_LIMIT,))

    def list_payload(self, limit: int = 200) -> dict:
        rows = self.db.list_shares(limit)
        items = []
        for row in rows:
            entry = {
                "id": row["id"],
                "kind": row["kind"],
                "title": row["title"],
                "artist": row["artist"],
                "duration": row["duration"],
                "playUrl": row["play_url"],
                "cover": row["cover"],
                "from": row["from_name"],
                "sharedAt": int(row["shared_at"] or 0),
            }
            items.append(self._payload(entry))
        return {"format": "muYunShared/1", "items": items}


def _as_duration(value) -> float | None:
    if value in (None, "", 0):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 3) if number > 0 else None


def parse_multipart_minimal(environ, content_type: str, content_length: int) -> tuple[dict, dict]:
    """Fallback multipart parser for WSGI servers that do not populate files."""
    import re

    boundary_match = re.search(r'boundary="?([^";]+)"?', content_type or "", re.I)
    if not boundary_match:
        raise ShareError("multipart 缺少 boundary")
    body = environ["wsgi.input"].read(content_length)
    boundary = b"--" + boundary_match.group(1).encode("latin-1")
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in body.split(boundary):
        if not part or part.startswith(b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        disposition = ""
        for line in head.decode("utf-8", "replace").split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                disposition = line
                break
        name_match = re.search(r'\bname="([^"]*)"', disposition)
        if not name_match:
            continue
        filename = re.search(r'\bfilename="([^"]*)"', disposition)
        if filename:
            files[name_match.group(1)] = (filename.group(1), data)
        else:
            fields[name_match.group(1)] = data.decode("utf-8", "replace")
    return fields, files
