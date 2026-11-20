"""HTTP API: the NAS-DEPLOY contract plus media-library extensions."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Blueprint, current_app, jsonify, request, send_file

from ..media import lrc_response, send_media
from ..share import ShareError
from ..util import (
    KIND_BY_EXT,
    media_url,
    resolve_rel,
    safe_rel,
    sanitize_filename,
    valid_host,
)

log = logging.getLogger("mediaserver.api")
bp = Blueprint("api", __name__, url_prefix="/api/v1")

ID_RE = re.compile(r"^/relay/(url|pic|lrc)/([A-Za-z0-9_-][A-Za-z0-9._-]{0,63})/([A-Za-z0-9_-][A-Za-z0-9._-]{0,63})$")
ITEM_ID_RE = re.compile(r"^[a-f0-9]{20}$")


def services():
    return current_app.extensions["mediaserver"]


def ok(payload: dict, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    return response


def fail(message: str, status: int = 400):
    return ok({"error": message}, status)


def request_base() -> str:
    cfg = services().config
    if cfg.public_url:
        return cfg.public_url
    host = request.host
    if cfg.trust_proxy:
        forwarded = request.headers.get("X-Forwarded-Host")
        if forwarded and valid_host(forwarded):
            host = forwarded.split(",")[0].strip()
    return cfg.public_base(host if valid_host(host) else f"{cfg.host}:{cfg.port}")


# ── contract: status ────────────────────────────────────────────────
@bp.get("/status")
def status():
    s = services()
    cfg = s.config
    counts = s.db.counts()
    scan = s.library.scan_status()
    features = {
        "music": True,
        "video": True,
        "hls": s.video.enabled,
        "search": True,
        "share": True,
        "relay": bool(cfg.relay_enabled),
        "auth": cfg.auth_enabled,
        "ffmpeg": s.library.tools.ffmpeg is not None,
        "ffprobe": s.library.tools.ffprobe is not None,
    }
    return ok({
        "ok": True,
        "gateway": cfg.server_name,
        "gatewayVersion": cfg.version,
        "serverName": cfg.server_name,
        "time": int(time.time()),
        "counts": counts,
        "scan": scan,
        "features": features,
        "share": {"format": "muYunShared/1", "enabled": True,
                  "maxUploadMB": cfg.share_max_mb},
        "upload": {"maxUploadMB": cfg.upload_max_mb},
        "search": {"format": "muYunSearch/1", "sources": list(cfg.search_sources)},
        "video": {"profiles": [f"{cfg.hls_height}p"], "segmentSeconds": cfg.hls_segment},
        "version": cfg.version,
    })


@bp.get("/healthz")
def healthz():
    return ok({"ok": True})


# ── contract: manifest ──────────────────────────────────────────────
@bp.get("/manifest")
def manifest():
    s = services()
    base = request_base()
    tracks = []
    for row in s.db.audio_for_manifest():
        meta = {}
        try:
            meta = json.loads(row["meta"]) if row["meta"] else {}
        except Exception:
            meta = {}
        track = {
            "url": media_url(row["rel"], base),
            "title": row["title"] or Path(row["rel"]).stem,
        }
        if row["artist"]:
            track["artist"] = row["artist"]
        if row["album"]:
            track["album"] = row["album"]
        if row["duration"]:
            track["duration"] = round(row["duration"], 2)
        if row["has_cover"]:
            track["cover"] = f"{base}/api/v1/cover/{row['id']}?v={int(row['mtime'])}"
        tags = []
        ext = Path(row["rel"]).suffix.lower().lstrip(".")
        if ext:
            tags.append(ext)
        if meta.get("sample_rate"):
            tags.append(f"{int(meta['sample_rate']) // 1000}kHz")
        if tags:
            track["tags"] = tags
        tracks.append(track)
    return ok({"schema": "muYunImport/1", "generatedAt": int(time.time()), "tracks": tracks})


# ── contract: search ────────────────────────────────────────────────
@bp.get("/search")
def search():
    s = services()
    query = (request.args.get("q") or "").strip()
    if not query:
        return fail("缺少参数 q", 400)
    try:
        limit = max(1, min(100, int(request.args.get("limit", 30))))
    except ValueError:
        limit = 30
    try:
        offset = max(0, min(20_000, int(request.args.get("offset", 0))))
    except ValueError:
        offset = 0
    requested = [src.strip() for src in (request.args.get("sources") or
                                        ",".join(s.config.search_sources)).split(",") if src.strip()]
    allowed = set(s.config.search_sources)
    usable = [src for src in requested if src in allowed]
    if not usable:
        return fail(f"不支持的音源:{','.join(requested)}", 400)

    results: list[dict] = []
    source_status: dict[str, dict] = {}
    for src in usable:
        if src == "local":
            rows, total = s.library.search_local(query, limit, offset)
            results.extend(rows)
            source_status[src] = {"ok": True, "count": len(rows), "total": total}
        else:
            source_status[src] = {"ok": False, "error": "source not enabled"}
    if not results and not any(v.get("ok") for v in source_status.values()):
        return fail(";".join(f"{k}:{v.get('error')}" for k, v in source_status.items()), 502)
    return ok({
        "format": "muYunSearch/1",
        "query": query,
        "sources": [src for src, meta in source_status.items() if meta.get("ok")],
        "results": results,
        "offset": offset,
        "limit": limit,
        "sourceErrors": {k: v for k, v in source_status.items() if not v.get("ok")},
    })


# ── extension: library ──────────────────────────────────────────────
@bp.get("/library")
def library():
    s = services()
    base = request_base()
    sections: dict[str, dict] = {}
    for kind in ("audio", "video", "image", "doc"):
        rows = s.db.list_items(kind=kind, sort="name", limit=10_000)
        sections[kind] = {
            "count": len(rows),
            "items": [s.library.item_payload(row, base) for row in rows],
        }
    return ok({
        "format": "mediaserver/library/1",
        "server": s.config.server_name,
        "version": s.config.version,
        "base": base,
        "counts": s.db.counts(),
        "sections": sections,
    })


@bp.get("/items")
def items():
    s = services()
    kind = request.args.get("kind")
    if kind not in (None, "", "audio", "video", "image", "doc"):
        return fail("未知的 kind", 400)
    kind = kind or None
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "recent")
    try:
        limit = max(1, min(200, int(request.args.get("limit", 40))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return fail("limit/offset 必须是整数", 400)
    rows = s.db.list_items(kind=kind, q=q, sort=sort, limit=limit, offset=offset,
                           artist=request.args.get("artist"), album=request.args.get("album"))
    total = s.db.count_items(kind=kind, q=q, artist=request.args.get("artist"),
                             album=request.args.get("album"))
    base = request_base()
    return ok({
        "kind": kind,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [s.library.item_payload(row, base) for row in rows],
    })


@bp.get("/items/<item>")
def item_detail(item):
    s = services()
    if not ITEM_ID_RE.fullmatch(item or ""):
        return fail("item id 格式不对", 400)
    row = s.db.get(item)
    if row is None:
        return fail("not found", 404)
    base = request_base()
    payload = s.library.item_payload(row, base)
    payload["playlists"] = []
    progress = s.db.get_progress(item)
    if progress:
        payload["position"] = progress["position"]
        payload["plays"] = progress["plays"]
    payload["related"] = [
        s.library.item_payload(other, base)
        for other in s.db.list_items(kind=row["kind"], album=row["album"] or None,
                                     limit=12) if other["id"] != row["id"]
    ]
    for playlist in s.db.list_playlists():
        if playlist["n"]:
            ids = {r["id"] for r in s.db.playlist_items(playlist["id"])}
            if item in ids:
                payload["playlists"].append({"id": playlist["id"], "name": playlist["name"]})
    return ok(payload)


@bp.get("/cover/<item>")
def cover(item):
    s = services()
    row = s.db.get(item)
    if row is None or not row["has_cover"]:
        return fail("no cover", 404)
    found = s.library.cover_file(row)
    if found is None:
        return fail("no cover", 404)
    path, mime = found
    response = send_file(path, mimetype=mime, conditional=True,
                         max_age=86_400, etag=True, last_modified=path.stat().st_mtime)
    response.headers.setdefault("Cache-Control", "public, max-age=86400")
    return response


@bp.get("/thumb/<item>")
def thumb(item):
    s = services()
    row = s.db.get(item)
    if row is None:
        return fail("not found", 404)
    try:
        width = int(request.args.get("w", 480))
    except ValueError:
        width = 480
    path = s.library.thumb_file(row, width)
    if path is None:
        found = s.library.cover_file(row)
        if found is None:
            return fail("no thumbnail", 404)
        path = found[0]
    return send_file(path, mimetype="image/jpeg", conditional=True, max_age=86_400,
                     etag=True, last_modified=path.stat().st_mtime)


@bp.get("/lyrics/<item>")
def lyrics(item):
    s = services()
    row = s.db.get(item)
    if row is None:
        return fail("not found", 404)
    text = s.library.lyrics_text(row)
    if not text:
        return fail("no lyrics", 404)
    if request.args.get("format") == "json":
        from ..util import parse_lrc

        parsed = parse_lrc(text)
        parsed["text"] = parsed["text"]
        return ok({"itemId": item, **parsed})
    return lrc_response(text)


# ── contract: download ──────────────────────────────────────────────
@bp.get("/download")
def download():
    s = services()
    target = request.args.get("target", "app")
    if target == "server":
        return fail("本服务器不提供第三方下载落库(target=server);请用 /media/ 直链", 501)
    item = request.args.get("item")
    src = request.args.get("src")
    row = None
    rel = None
    if item and ITEM_ID_RE.fullmatch(item):
        row = s.db.get(item)
        if row is None:
            return fail("not found", 404)
        rel = row["rel"]
        path = s.library.resolve(row)
    elif src:
        parsed = urlparse(src)
        path_part = parsed.path
        if not path_part.startswith("/media/"):
            return fail("src 必须是 /media/<path>", 400)
        rel = safe_rel(path_part[len("/media/"):])
        path = resolve_rel(s.config.mounts, rel or "")
    else:
        return fail("缺少 item 或 src", 400)
    if path is None or rel is None:
        return fail("not found", 404)
    name = row["title"] if row is not None else Path(rel).stem
    filename = sanitize_filename(f"{name}{path.suffix}", fallback=path.name)
    return send_media(path, as_attachment=True, download_name=filename,
                      max_age=0)


# ── contract: shares ────────────────────────────────────────────────
@bp.get("/shared")
def shared():
    return ok(services().share.list_payload())


@bp.post("/share")
def create_share():
    s = services()
    size_limit = s.config.share_max_mb * 1024 * 1024
    length = request.content_length or 0
    if length > size_limit:
        return fail(f"文件超过分享上限 {s.config.share_max_mb} MB", 413)
    content_type = request.content_type or ""
    try:
        if content_type.startswith("multipart/form-data"):
            upload = request.files.get("file")
            fields = {key: request.form.get(key) for key in
                      ("kind", "title", "artist", "duration", "from")}
            payload = s.share.create_from_file(file_storage=upload, fields=fields)
        else:
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return fail("需要 multipart/form-data 或 JSON", 400)
            payload = s.share.create_from_url(body, request.host)
    except ShareError as exc:
        return fail(str(exc), exc.code)
    return ok(payload, 201)


# ── extension: rescan / upload ──────────────────────────────────────
@bp.post("/rescan")
def rescan():
    import threading

    s = services()
    threading.Thread(target=s.library.scan, name="manual-scan", daemon=True).start()
    return ok({"started": True}, 202)


@bp.post("/upload")
def upload():
    import threading

    s = services()
    if not request.files:
        return fail("缺少 file 字段", 400)
    upload_file = request.files.get("file")
    if upload_file is None or not upload_file.filename:
        return fail("缺少 file 字段", 400)
    original = sanitize_filename(upload_file.filename, "upload")
    suffix = Path(original).suffix.lower()
    kind = KIND_BY_EXT.get(suffix)
    if kind is None:
        return fail(f"不支持的文件类型:{suffix or '(无后缀)'}", 415)
    folder = {"audio": "music", "video": "videos", "image": "images", "doc": "docs"}[kind]
    destination_dir = s.config.uploads_dir / folder
    destination_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(original).stem
    candidate = destination_dir / original
    counter = 1
    while candidate.exists():
        candidate = destination_dir / f"{stem}-{counter}{suffix}"
        counter += 1
    temp = candidate.with_suffix(candidate.suffix + ".part")
    upload_file.save(temp)
    size = temp.stat().st_size
    if size == 0:
        temp.unlink(missing_ok=True)
        return fail("空文件", 400)
    if size > s.config.upload_max_mb * 1024 * 1024:
        temp.unlink(missing_ok=True)
        return fail(f"文件超过上传上限 {s.config.upload_max_mb} MB", 413)
    os.replace(temp, candidate)
    threading.Thread(target=s.library.scan, name="upload-scan", daemon=True).start()
    rel = f"uploads/{folder}/{candidate.name}"
    return ok({"uploaded": True, "rel": rel, "size": size,
               "url": media_url(rel), "kind": kind}, 201)


# ── extension: favorites ───────────────────────────────────────────
@bp.get("/favorites")
def favorites():
    s = services()
    try:
        limit = max(1, min(500, int(request.args.get("limit", 200))))
    except ValueError:
        limit = 200
    base = request_base()
    return ok({"items": [s.library.item_payload(row, base)
                         for row in s.db.favorite_rows(limit=limit)]})


@bp.put("/favorites/<item>")
def favorite_add(item):
    s = services()
    if not ITEM_ID_RE.fullmatch(item or "") or s.db.get(item) is None:
        return fail("item id 无效", 400)
    s.db.set_favorite(item, True)
    return ok({"itemId": item, "favorite": True})


@bp.delete("/favorites/<item>")
def favorite_remove(item):
    s = services()
    if not ITEM_ID_RE.fullmatch(item or ""):
        return fail("item id 格式不对", 400)
    s.db.set_favorite(item, False)
    return ok({"itemId": item, "favorite": False})


# ── extension: albums / artists ────────────────────────────────────
@bp.get("/albums")
def albums():
    s = services()
    rows = s.db.albums(limit=max(1, min(1000, int(request.args.get("limit", 300)))))
    return ok({"albums": [
        {"name": row["album"], "artist": row["artist"], "count": row["n"],
         "cover": f"/api/v1/cover/{row['cover_id']}" if row["cover_id"] else None}
        for row in rows
    ]})


@bp.get("/artists")
def artists():
    s = services()
    rows = s.db.artists(limit=max(1, min(1000, int(request.args.get("limit", 300)))))
    return ok({"artists": [
        {"name": row["artist"], "count": row["n"], "albums": row["albums"],
         "cover": f"/api/v1/cover/{row['cover_id']}" if row["cover_id"] else None}
        for row in rows
    ]})


# ── extension: progress & playlists ────────────────────────────────
@bp.post("/progress")
def save_progress():
    s = services()
    body = request.get_json(silent=True) or {}
    item = body.get("itemId") or body.get("id")
    if not item or not ITEM_ID_RE.fullmatch(str(item)):
        return fail("itemId 无效", 400)
    if s.db.get(str(item)) is None:
        return fail("not found", 404)
    try:
        position = max(0.0, float(body.get("position", 0)))
    except (TypeError, ValueError):
        return fail("position 必须是数字", 400)
    try:
        duration = float(body["duration"]) if body.get("duration") is not None else None
    except (TypeError, ValueError):
        duration = None
    s.db.set_progress(str(item), position, duration, played=bool(body.get("played")))
    return ok({"saved": True})


@bp.get("/progress/recent")
def recent_progress():
    s = services()
    kind = request.args.get("kind")
    if kind not in (None, "", "audio", "video"):
        return fail("kind 必须是 audio 或 video", 400)
    try:
        limit = max(1, min(50, int(request.args.get("limit", 12))))
    except ValueError:
        limit = 12
    rows = s.db.recent_progress(limit, kind or None)
    base = request_base()
    items_out = []
    for row in rows:
        payload = s.library.item_payload(row, base)
        payload["position"] = row["position"]
        payload["progressAt"] = int(row["progress_at"] or 0)
        items_out.append(payload)
    return ok({"items": items_out})


@bp.get("/playlists")
def playlists():
    s = services()
    return ok({"playlists": [
        {"id": row["id"], "name": row["name"], "count": row["n"],
         "createdAt": int(row["created_at"])}
        for row in s.db.list_playlists()
    ]})


@bp.post("/playlists")
def create_playlist():
    s = services()
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()[:80]
    if not name:
        return fail("缺少 name", 400)
    playlist_id = "pl-" + secrets.token_hex(6)
    s.db.create_playlist(playlist_id, name)
    return ok({"id": playlist_id, "name": name}, 201)


@bp.get("/playlists/<playlist_id>")
def playlist_detail(playlist_id):
    s = services()
    base = request_base()
    rows = s.db.playlist_items(playlist_id)
    if not rows:
        meta = [row for row in s.db.list_playlists() if row["id"] == playlist_id]
        if not meta:
            return fail("not found", 404)
    return ok({
        "id": playlist_id,
        "items": [s.library.item_payload(row, base) for row in rows],
    })


@bp.post("/playlists/<playlist_id>/items")
def playlist_add(playlist_id):
    s = services()
    body = request.get_json(silent=True) or {}
    item = str(body.get("itemId") or "")
    if not ITEM_ID_RE.fullmatch(item) or s.db.get(item) is None:
        return fail("itemId 无效", 400)
    s.db.add_to_playlist(playlist_id, item)
    return ok({"added": True})


@bp.delete("/playlists/<playlist_id>/items/<item>")
def playlist_remove(playlist_id, item):
    services().db.remove_from_playlist(playlist_id, item)
    return ok({"removed": True})


@bp.delete("/playlists/<playlist_id>")
def playlist_delete(playlist_id):
    services().db.delete_playlist(playlist_id)
    return ok({"deleted": True})


# ── extension: video / HLS ─────────────────────────────────────────
@bp.post("/transcode/<item>")
def transcode(item):
    s = services()
    row = s.db.get(item)
    if row is None:
        return fail("not found", 404)
    if not s.video.enabled:
        return fail("HLS 转码未启用", 501)
    body = request.get_json(silent=True) or {}
    profile = body.get("profile")
    if profile is not None and not re.fullmatch(r"[0-9]{3,4}p", str(profile)):
        return fail("profile 格式不对", 400)
    try:
        job = s.video.start(row, profile)
    except (RuntimeError, ValueError) as exc:
        return fail(str(exc), 400)
    return ok(job, 202 if job["status"] != "ready" else 200)


@bp.get("/jobs/<job_id>")
def job_status(job_id):
    s = services()
    if not re.fullmatch(r"[a-f0-9]{20}-[0-9]{3,4}p", job_id):
        return fail("job id 格式不对", 400)
    item, _, profile = job_id.rpartition("-")
    row = s.db.get(item)
    if row is None:
        return fail("not found", 404)
    return ok(s.video.status(row, profile))


@bp.get("/hls/<job_id>/index.m3u8")
def hls_playlist(job_id):
    s = services()
    text = s.video.playlist(job_id)
    if text is None:
        return fail("转码尚未完成", 409)
    return ok_raw(text, "application/vnd.apple.mpegurl")


@bp.get("/hls/<job_id>/<segment>")
def hls_segment(job_id, segment):
    s = services()
    path = s.video.segment(job_id, segment)
    if path is None:
        return fail("not found", 404)
    return send_file(path, mimetype="video/mp2t", conditional=True, max_age=3600)


def ok_raw(text: str, mimetype: str):
    from flask import Response

    response = Response(text, mimetype=mimetype)
    response.headers["Cache-Control"] = "no-store"
    return response


# ── optional: Meting relay ─────────────────────────────────────────
RELAY_CACHE: dict[str, tuple[float, bytes]] = {}


@bp.get("/relay/<typ>/<server>/<ident>")
def relay(typ: str, server: str, ident: str):
    s = services()
    cfg = s.config
    if not cfg.relay_enabled:
        return fail("relay 未启用(MEDIA_RELAY_ENABLED=1)", 404)
    if typ not in ("url", "pic", "lrc"):
        return fail("type 必须是 url/pic/lrc", 400)
    if not all(re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}", part) for part in (server, ident)):
        return fail("参数格式不对", 400)
    from urllib.parse import urlencode

    upstream = cfg.relay_base + "?" + urlencode({"server": server, "type": typ, "id": ident})
    if typ in ("pic", "lrc"):
        key = f"{typ}:{server}:{ident}"
        cached = RELAY_CACHE.get(key)
        if cached and time.time() - cached[0] < 86_400:
            return _relay_bytes(cached[1], typ)
    referer = {"netease": "https://music.163.com/", "tencent": "https://y.qq.com/"}.get(server)
    headers = {"User-Agent": "mediaserver/1.0", "Referer": referer} if referer else {"User-Agent": "mediaserver/1.0"}
    range_header = request.headers.get("Range")
    if typ == "url" and range_header:
        headers["Range"] = range_header
    try:
        response = _fetch_with_guards(upstream, headers, allow_private=cfg.relay_allow_private,
                                      timeout=cfg.relay_timeout)
    except Exception as exc:
        return fail(f"上游不可达:{exc}", 502)
    try:
        if typ == "url":
            status_code = response.status_code
            flask_response = current_app.response_class(
                _iter_content(response), status=status_code,
                mimetype=response.headers.get("Content-Type", "audio/mpeg"))
            for header in ("Content-Length", "Content-Range", "Accept-Ranges"):
                if header in response.headers:
                    flask_response.headers[header] = response.headers[header]
            flask_response.headers["Cache-Control"] = "no-store"
            # The generator closes the upstream when the client disconnects.
            flask_response.call_on_close(response.close)
            return flask_response
        data = response.content
        response.close()
        if len(data) > 5 * 1024 * 1024:
            return fail("上游响应过大", 502)
        RELAY_CACHE[key] = (time.time(), data)
        if len(RELAY_CACHE) > 400:
            oldest = sorted(RELAY_CACHE.items(), key=lambda kv: kv[1][0])[:100]
            for k, _ in oldest:
                RELAY_CACHE.pop(k, None)
        return _relay_bytes(data, typ)
    except Exception:
        response.close()
        raise


def _relay_bytes(data: bytes, typ: str):
    from flask import Response

    mime = "image/jpeg" if typ == "pic" else "text/plain; charset=utf-8"
    response = Response(data, mimetype=mime)
    response.headers["Cache-Control"] = "public, max-age=86400" if typ == "pic" else "public, max-age=3600"
    return response


def _iter_content(response, chunk: int = 64 * 1024):
    try:
        for block in response.iter_content(chunk):
            if block:
                yield block
    finally:
        response.close()


def _fetch_with_guards(url: str, headers: dict, *, allow_private: bool, timeout: int):
    """Follow redirects manually so every hop is checked against SSRF."""
    from ..util import host_is_private

    current = url
    for _ in range(6):
        parsed = urlparse(current)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("unsupported scheme")
        if host_is_private(parsed.hostname) and not allow_private:
            raise ValueError("upstream resolves to a private address")
        response = requests.get(current, headers=headers, timeout=timeout,
                                allow_redirects=False, stream=True)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("redirect without Location")
            current = _join_url(current, location)
            continue
        return response
    raise ValueError("too many redirects")


def _join_url(base: str, location: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, location)


