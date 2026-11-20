"""Indexing, cover art, thumbnails and lyrics for the media library."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from .config import Config
from .db import Database
from .ffmpeg import FfmpegTools, discover, run
from .metadata import display_title, extract_cover, probe
from .util import (
    KIND_BY_EXT,
    item_id,
    kind_for,
    media_url,
    mime_for,
    rel_for_path,
    rel_to_url_path,
    resolve_rel,
)

log = logging.getLogger("mediaserver.library")

INDEX_MOUNTS = ("music", "videos", "images", "docs", "uploads")
COVER_CACHE_MAX_MB = 512


class LibraryManager:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.tools: FfmpegTools = discover(config.ffmpeg, config.ffprobe)
        self._scan_lock = threading.Lock()
        self._scan_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._rescan_requested = threading.Event()
        self._thumb_locks: dict[str, threading.Lock] = {}
        self._thumb_locks_guard = threading.Lock()

    # ── scanning ────────────────────────────────────────────────────
    def _iter_files(self) -> list[tuple[str, Path]]:
        found: dict[str, Path] = {}
        mounts = self.config.mounts
        for mount in INDEX_MOUNTS:
            root = mounts.get(mount)
            if root is None or not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_symlink():
                    # Resolve only files that stay inside the mount; never follow
                    # directory symlinks (rglob does not recurse into them).
                    real = path.resolve()
                    if not real.is_file():
                        continue
                elif not path.is_file():
                    continue
                if path.suffix.lower() not in KIND_BY_EXT:
                    continue
                rel = rel_for_path({mount: root}, path)
                if rel is None:
                    continue
                found[rel] = path
        return sorted(found.items())

    def scan(self) -> dict:
        """Incrementally index the library. Safe to call concurrently."""
        if not self._scan_lock.acquire(blocking=False):
            # A scan is in flight; make sure another one follows so uploads
            # arriving mid-scan are picked up instead of waiting for the timer.
            self._rescan_requested.set()
            return {"running": True, "skipped": True}
        started = time.time()
        stats = {"scanned": 0, "added": 0, "updated": 0, "removed": 0, "errors": 0}
        try:
            existing = self.db.existing()
            seen: set[str] = set()
            for rel, path in self._iter_files():
                seen.add(rel)
                kind = kind_for(path)
                if kind is None:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    stats["errors"] += 1
                    continue
                row = existing.get(rel)
                if row is not None and abs(row["mtime"] - stat.st_mtime) < 0.001 \
                        and row["size"] == stat.st_size:
                    continue
                try:
                    meta = probe(path, kind, self.tools)
                except Exception as exc:  # a broken file must not stop the scan
                    log.warning("probe failed for %s: %s", rel, exc)
                    meta = {"title": display_title(path.stem), "has_cover": False,
                            "has_lyrics": False, "duration": None}
                    stats["errors"] += 1
                cover_rel = rel_for_path(self.config.mounts, meta["cover_path"]) \
                    if meta.get("cover_path") else None
                lyrics_rel = rel_for_path(self.config.mounts, meta["lyrics_path"]) \
                    if meta.get("lyrics_path") else None
                clean_meta = {
                    key: value for key, value in meta.items()
                    if key not in ("cover_path", "lyrics_path", "lyrics_text")
                }
                if meta.get("lyrics_text"):
                    clean_meta["embedded_lyrics"] = True
                record = {
                    "id": item_id(rel),
                    "rel": rel,
                    "kind": kind,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "title": meta.get("title") or display_title(path.stem),
                    "artist": meta.get("artist") or "",
                    "album": meta.get("album") or "",
                    "albumartist": meta.get("albumartist") or "",
                    "duration": meta.get("duration"),
                    "width": meta.get("width"),
                    "height": meta.get("height"),
                    "video_codec": meta.get("video_codec"),
                    "audio_codec": meta.get("audio_codec"),
                    "bitrate": meta.get("bitrate"),
                    "sample_rate": meta.get("sample_rate"),
                    "has_cover": 1 if meta.get("has_cover") else 0,
                    "has_lyrics": 1 if meta.get("has_lyrics") else 0,
                    "lyrics_rel": lyrics_rel,
                    "cover_rel": cover_rel,
                    "meta": clean_meta,
                    "added_at": row["added_at"] if row is not None else time.time(),
                    "indexed_at": time.time(),
                }
                # Store embedded lyrics so they survive without re-reading the file.
                if meta.get("lyrics_text"):
                    record["meta"]["embedded_lyrics_text"] = meta["lyrics_text"][:200_000]
                self.db.upsert_item(record)
                stats["scanned"] += 1
                if row is None:
                    stats["added"] += 1
                else:
                    stats["updated"] += 1
            stats["removed"] = self.db.delete_missing(seen)
            stats["duration"] = round(time.time() - started, 2)
            stats["finished_at"] = time.time()
            stats["counts"] = self.db.counts()
            self.db.state_set("last_scan", stats)
            log.info("scan finished: %s", stats)
            return stats
        finally:
            self._scan_lock.release()
            if self._rescan_requested.is_set():
                self._rescan_requested.clear()
                threading.Thread(target=self.scan, name="media-rescan", daemon=True).start()

    def start(self) -> None:
        if self.config.scan_on_start:
            self._scan_thread = threading.Thread(target=self._scan_loop, name="media-scan",
                                                 daemon=True)
            self._scan_thread.start()

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception:
                log.exception("library scan failed")
            interval = self.config.scan_interval
            if interval <= 0:
                return
            self._stop.wait(interval)

    def stop(self) -> None:
        self._stop.set()

    def scan_status(self) -> dict:
        state = self.db.state_get("last_scan") or {}
        return {
            "lastScan": state.get("finished_at"),
            "lastScanDuration": state.get("duration"),
            "scanning": self._scan_lock.locked(),
            "counts": self.db.counts(),
            "interval": self.config.scan_interval,
        }

    # ── item conversion ─────────────────────────────────────────────
    def resolve(self, row) -> Path | None:
        return resolve_rel(self.config.mounts, row["rel"])

    def item_payload(self, row, base: str = "") -> dict:
        meta = {}
        try:
            meta = json.loads(row["meta"]) if row["meta"] else {}
        except Exception:
            meta = {}
        raw_url = rel_to_url_path(row["rel"])
        payload = {
            "id": row["id"],
            "kind": row["kind"],
            "rel": row["rel"],
            "url": media_url(row["rel"], base),
            "rawUrl": raw_url,
            "title": row["title"] or display_title(Path(row["rel"]).stem),
            "artist": row["artist"] or "",
            "album": row["album"] or "",
            "albumartist": row["albumartist"] or "",
            "duration": row["duration"],
            "width": row["width"],
            "height": row["height"],
            "videoCodec": row["video_codec"],
            "audioCodec": row["audio_codec"],
            "bitrate": row["bitrate"],
            "sampleRate": row["sample_rate"],
            "size": row["size"],
            "mtime": int(row["mtime"]),
            "mime": mime_for(Path(row["rel"])),
            "cover": f"/api/v1/cover/{row['id']}?v={int(row['mtime'])}" if row["has_cover"] else None,
            "lyricsUrl": f"/api/v1/lyrics/{row['id']}" if row["has_lyrics"] else None,
            "hasCover": bool(row["has_cover"]),
            "hasLyrics": bool(row["has_lyrics"]),
            "genre": meta.get("genre") or "",
            "year": meta.get("year") or "",
        }
        if row["kind"] == "video" and self.tools.can_thumbnail:
            payload["thumb"] = f"/api/v1/thumb/{row['id']}?v={int(row['mtime'])}"
        if row["kind"] == "image":
            payload["thumb"] = f"/api/v1/thumb/{row['id']}?w=480&v={int(row['mtime'])}"
        return payload

    def search_local(self, q: str, limit: int = 30, offset: int = 0) -> tuple[list[dict], int]:
        rows = self.db.list_items(q=q, sort="recent", limit=limit, offset=offset)
        total = self.db.count_items(q=q)
        return [self._search_row(row) for row in rows], total

    def _search_row(self, row) -> dict:
        duration = round(row["duration"], 1) if row["duration"] else None
        entry = {
            "id": f"local:{row['rel']}",
            "itemId": row["id"],
            "title": row["title"] or display_title(Path(row["rel"]).stem),
            "artist": row["artist"] or "",
            "album": row["album"] or "",
            "duration": duration,
            "playUrl": rel_to_url_path(row["rel"]),
            "source": "local",
            "kind": row["kind"],
        }
        if row["has_cover"]:
            entry["cover"] = f"/api/v1/cover/{row['id']}?v={int(row['mtime'])}"
        if row["has_lyrics"]:
            entry["lyricsUrl"] = f"/api/v1/lyrics/{row['id']}"
        entry["download"] = "/api/v1/download?format=raw&item=" + row["id"]
        return entry

    # ── artwork ─────────────────────────────────────────────────────
    def _cover_path(self, row) -> Path | None:
        if row["cover_rel"]:
            resolved = resolve_rel(self.config.mounts, row["cover_rel"])
            if resolved is not None:
                return resolved
        return None

    def cover_file(self, row) -> tuple[Path, str] | None:
        """Return a JPEG cover for an item, extracting it into the cache if needed."""
        if not row["has_cover"]:
            return None
        cache = self.config.cover_cache_dir / f"{row['id']}.jpg"
        if cache.is_file() and cache.stat().st_size > 0:
            return cache, "image/jpeg"
        source = self._cover_path(row)
        if source is not None:
            try:
                from PIL import Image

                cache.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(source) as image:
                    image = image.convert("RGB")
                    image.thumbnail((1000, 1000))
                    image.save(cache, "JPEG", quality=86, optimize=True)
                return cache, "image/jpeg"
            except Exception:
                # Serving the original is better than no cover.
                return source, mime_for(source)
        media = self.resolve(row)
        if media is None:
            return None
        temp = cache.with_suffix(".tmp.jpg")
        if extract_cover(media, row["kind"], temp, self.tools):
            try:
                temp.replace(cache)
            except OSError:
                temp.unlink(missing_ok=True)
            if cache.is_file() and cache.stat().st_size > 0:
                return cache, "image/jpeg"
        return None

    def thumb_file(self, row, width: int = 480) -> Path | None:
        width = max(64, min(1600, int(width)))
        cache = self.config.thumb_cache_dir / f"{row['id']}-{width}.jpg"
        if cache.is_file() and cache.stat().st_size > 0:
            return cache
        key = f"{row['id']}-{width}"
        with self._thumb_locks_guard:
            lock = self._thumb_locks.setdefault(key, threading.Lock())
        with lock:
            if cache.is_file() and cache.stat().st_size > 0:
                return cache
            ok = False
            media = self.resolve(row)
            if media is not None and row["kind"] == "video" and self.tools.can_thumbnail:
                cache.parent.mkdir(parents=True, exist_ok=True)
                seek = min(5.0, max(0.0, (row["duration"] or 10) * 0.1))
                proc = run([self.tools.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                            "-ss", str(seek), "-i", str(media), "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-q:v", "3", str(cache)], timeout=30)
                ok = proc.returncode == 0 and cache.is_file() and cache.stat().st_size > 0
            elif media is not None and row["kind"] == "image":
                try:
                    from PIL import Image

                    cache.parent.mkdir(parents=True, exist_ok=True)
                    with Image.open(media) as image:
                        image = image.convert("RGB")
                        image.thumbnail((width, width))
                        image.save(cache, "JPEG", quality=84, optimize=True)
                    ok = True
                except Exception:
                    ok = False
            elif media is not None:
                source = self.cover_file(row)
                if source is not None:
                    try:
                        from PIL import Image

                        cache.parent.mkdir(parents=True, exist_ok=True)
                        with Image.open(source[0]) as image:
                            image = image.convert("RGB")
                            image.thumbnail((width, width))
                            image.save(cache, "JPEG", quality=84, optimize=True)
                        ok = True
                    except Exception:
                        ok = False
            if not ok:
                cache.unlink(missing_ok=True)
                return None
            self._evict(self.config.thumb_cache_dir, self.config.thumb_cache_mb)
            return cache

    def lyrics_text(self, row) -> str:
        if row["lyrics_rel"]:
            resolved = resolve_rel(self.config.mounts, row["lyrics_rel"])
            if resolved is not None:
                try:
                    return resolved.read_text("utf-8", "replace")[:500_000]
                except OSError:
                    return ""
        try:
            meta = json.loads(row["meta"]) if row["meta"] else {}
        except Exception:
            meta = {}
        return meta.get("embedded_lyrics_text") or ""

    # ── uploaded files ─────────────────────────────────────────────
    def list_uploads(self, kind: str | None = None, q: str = "",
                     limit: int = 1000) -> list[dict]:
        root = self.config.uploads_dir
        if not root.is_dir():
            return []
        needle = (q or "").strip().casefold()
        rows: list[dict] = []
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            file_kind = kind_for(path)
            if file_kind is None or (kind and file_kind != kind):
                continue
            rel = "uploads/" + path.relative_to(root).as_posix()
            if needle and needle not in rel.casefold():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append({
                "rel": rel,
                "name": path.name,
                "dir": path.parent.relative_to(root).as_posix(),
                "kind": file_kind,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
                "url": rel_to_url_path(rel),
                "download": rel_to_url_path(rel),
            })
        rows.sort(key=lambda row: row["mtime"], reverse=True)
        return rows[:limit]

    def upload_counts(self) -> dict[str, int]:
        counts = {"audio": 0, "video": 0, "image": 0, "doc": 0, "total": 0}
        root = self.config.uploads_dir
        if not root.is_dir():
            return counts
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            file_kind = kind_for(path)
            if file_kind is None:
                continue
            counts[file_kind] += 1
            counts["total"] += 1
        return counts

    # ── cache maintenance ──────────────────────────────────────────
    @staticmethod
    def _evict(directory: Path, max_mb: int) -> None:
        try:
            files = [p for p in directory.iterdir() if p.is_file()]
        except OSError:
            return
        total = sum(p.stat().st_size for p in files if p.exists())
        if total <= max_mb * 1024 * 1024:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        for path in files:
            if total <= max_mb * 1024 * 1024:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
            except OSError:
                continue
