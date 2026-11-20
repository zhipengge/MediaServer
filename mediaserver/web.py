"""Server-rendered web client.

The pages are intentionally plain Jinja2 + small ES modules: no Node build
step, no CDN dependency, everything works on a LAN with no internet access.
"""

from __future__ import annotations

import math

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

bp = Blueprint("web", __name__)

KIND_LABELS = {"audio": "音乐", "video": "视频", "image": "图片", "doc": "文档"}
PER_PAGE = 36


def svc():
    return current_app.extensions["mediaserver"]


def _row_or_none(item: str):
    services = svc()
    if not item or len(item) != 20:
        return None
    return services.db.get(item)


def _page_args() -> tuple[str, str, int]:
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "recent")
    if sort not in {"recent", "mtime", "name", "artist", "duration", "size"}:
        sort = "recent"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    return q, sort, page


@bp.get("/")
def index():
    s = svc()
    base = s.config.public_base(request.host)
    recent = s.db.list_items(sort="recent", limit=12)
    videos = s.db.list_items(kind="video", sort="recent", limit=8)
    images = s.db.list_items(kind="image", sort="recent", limit=8)
    continue_audio = s.db.recent_progress(limit=6, kind="audio")
    continue_video = s.db.recent_progress(limit=6, kind="video")
    return render_template(
        "index.html",
        recent=[s.library.item_payload(row, base) for row in recent],
        videos=[s.library.item_payload(row, base) for row in videos],
        images=[s.library.item_payload(row, base) for row in images],
        continue_audio=[s.library.item_payload(row, base) | {"position": row["position"]}
                        for row in continue_audio],
        continue_video=[s.library.item_payload(row, base) | {"position": row["position"]}
                        for row in continue_video],
        counts=s.db.counts(),
        scan=s.library.scan_status(),
    )


@bp.get("/library")
def library_page():
    s = svc()
    base = s.config.public_base(request.host)
    q, sort, page = _page_args()
    kind = request.args.get("kind") or None
    if kind not in (None, "audio", "video", "image", "doc"):
        kind = None
    artist = (request.args.get("artist") or "").strip() or None
    album = (request.args.get("album") or "").strip() or None
    total = s.db.count_items(kind=kind, q=q, artist=artist, album=album)
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(page, pages)
    rows = s.db.list_items(kind=kind, q=q, sort=sort, limit=PER_PAGE,
                           offset=(page - 1) * PER_PAGE, artist=artist, album=album)
    facets = s.db.facets(kind) if kind in (None, "audio") else {"artists": [], "albums": []}
    return render_template(
        "library.html",
        items=[s.library.item_payload(row, base) for row in rows],
        kind=kind,
        kinds=KIND_LABELS,
        q=q,
        sort=sort,
        artist=artist,
        album=album,
        page=page,
        pages=pages,
        total=total,
        facets=facets,
    )


@bp.get("/listen/<item>")
def listen(item):
    s = svc()
    row = _row_or_none(item)
    if row is None:
        return render_template("error.html", code=404, message="曲目不存在"), 404
    base = s.config.public_base(request.host)
    queue_rows = []
    if row["album"]:
        queue_rows = s.db.list_items(kind="audio", album=row["album"], sort="artist", limit=60)
    if len(queue_rows) < 2:
        queue_rows = s.db.list_items(kind="audio", sort="name", limit=60)
    queue = [s.library.item_payload(other, base) for other in queue_rows]
    progress = s.db.get_progress(item)
    return render_template(
        "listen.html",
        item=s.library.item_payload(row, base),
        queue=queue,
        lyrics=s.library.lyrics_text(row) if row["has_lyrics"] else "",
        position=progress["position"] if progress else 0,
        playlists=[dict(entry) for entry in s.db.list_playlists()],
    )


@bp.get("/watch/<item>")
def watch(item):
    s = svc()
    row = _row_or_none(item)
    if row is None or row["kind"] != "video":
        return render_template("error.html", code=404, message="视频不存在"), 404
    base = s.config.public_base(request.host)
    payload = s.library.item_payload(row, base)
    progress = s.db.get_progress(item)
    return render_template(
        "watch.html",
        item=payload,
        direct=s.video.direct_playable(row),
        hls_enabled=s.video.enabled,
        position=progress["position"] if progress else 0,
    )


@bp.get("/view/<item>")
def view_image(item):
    s = svc()
    row = _row_or_none(item)
    if row is None or row["kind"] != "image":
        return render_template("error.html", code=404, message="图片不存在"), 404
    base = s.config.public_base(request.host)
    return render_template("image.html", item=s.library.item_payload(row, base))


@bp.get("/read/<item>")
def read_doc(item):
    s = svc()
    row = _row_or_none(item)
    if row is None or row["kind"] != "doc":
        return render_template("error.html", code=404, message="文档不存在"), 404
    base = s.config.public_base(request.host)
    payload = s.library.item_payload(row, base)
    path = s.library.resolve(row)
    text = ""
    if path is not None and path.suffix.lower() in {".txt", ".md", ".csv", ".json", ".lrc",
                                                    ".srt", ".vtt", ".m3u"}:
        try:
            text = path.read_text("utf-8", "replace")[:200_000]
        except OSError:
            text = ""
    return render_template("doc.html", item=payload, text=text,
                           is_text=bool(text))


@bp.get("/shares")
def shares():
    s = svc()
    return render_template("shares.html", shares=s.share.list_payload()["items"])


@bp.get("/playlists")
def playlists_page():
    s = svc()
    return render_template("playlists.html",
                           playlists=[dict(row) for row in s.db.list_playlists()])


@bp.get("/playlist/<playlist_id>")
def playlist_page(playlist_id):
    s = svc()
    base = s.config.public_base(request.host)
    rows = s.db.playlist_items(playlist_id)
    name = next((row["name"] for row in s.db.list_playlists()
                 if row["id"] == playlist_id), "播放列表")
    return render_template("playlist.html", playlist_id=playlist_id, name=name,
                           items=[s.library.item_payload(row, base) for row in rows])


@bp.get("/settings")
def settings_page():
    s = svc()
    cfg = s.config
    status = {
        "version": cfg.version,
        "name": cfg.server_name,
        "auth": cfg.auth_enabled,
        "cors": bool(cfg.cors_origin),
        "publicUrl": cfg.public_url or "(request host)",
        "scanInterval": cfg.scan_interval,
        "shareMaxMB": cfg.share_max_mb,
        "uploadMaxMB": cfg.upload_max_mb,
        "hlsEnabled": cfg.hls_enabled,
        "hlsHeight": cfg.hls_height,
        "hlsSegment": cfg.hls_segment,
        "ffmpeg": bool(s.library.tools.ffmpeg),
        "ffprobe": bool(s.library.tools.ffprobe),
        "relay": cfg.relay_enabled,
        "relayBase": cfg.relay_base if cfg.relay_enabled else "",
        "cookieSecure": cfg.cookie_secure,
        "counts": s.db.counts(),
        "scan": s.library.scan_status(),
        "dataDir": str(cfg.data_dir),
        "musicRoot": str(cfg.music_root),
    }
    return render_template("settings.html", status=status)


# ── auth pages ─────────────────────────────────────────────────────
@bp.get("/login")
def login():
    s = svc()
    if not s.config.auth_enabled:
        return redirect(url_for("web.index"))
    if session.get("auth") is True:
        return redirect(url_for("web.index"))
    next_path = request.args.get("next", "/")
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"
    return render_template("login.html", next=next_path,
                           error=request.args.get("error"))


@bp.post("/login")
def login_post():
    s = svc()
    if not s.config.auth_enabled:
        return redirect(url_for("web.index"))
    next_path = request.form.get("next", "/")
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"
    if s.auth.throttled():
        return redirect(url_for("web.login", next=next_path, error="尝试次数过多,请稍后再试"))
    token = request.form.get("token", "")
    if s.auth.matches(token):
        s.auth.clear()
        session.clear()
        session["auth"] = True
        session.permanent = False
        return redirect(next_path)
    s.auth.throttle_hit()
    return redirect(url_for("web.login", next=next_path, error="口令不正确"))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.index"))


@bp.get("/favicon.ico")
def favicon():
    return ("", 204)
