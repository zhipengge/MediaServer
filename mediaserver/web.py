"""Server-rendered web client.

The pages are intentionally plain Jinja2 + small ES modules: no Node build
step, no CDN dependency, everything works on a LAN with no internet access.
"""

from __future__ import annotations

import math
import time

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .auth import QR_LOGIN_TTL
from .util import artist_id as make_artist_id
from .util import artist_initial

bp = Blueprint("web", __name__)

KIND_LABELS = {"audio": "音乐", "video": "视频", "image": "图片", "doc": "文档"}
PER_PAGE = 36
MUSIC_TABS = ("songs", "albums", "artists", "favorites", "recent", "top")


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
    charts = {
        "recent": [s.library.item_payload(row, base) for row in recent[:5]],
        "played": [s.library.item_payload(row, base) for row in continue_audio[:5]],
        "favorites": [s.library.item_payload(row, base) for row in s.db.favorite_rows(limit=5)],
        "top": [s.library.item_payload(row, base) for row in s.db.top_played(limit=5)],
    }
    return render_template(
        "index.html",
        charts=charts,
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
    if kind == "audio":
        target = url_for("web.music_page")
        extra = {key: value for key, value in request.args.items() if key != "kind"}
        return redirect(url_for("web.music_page", **extra) if extra else target)
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


@bp.get("/music")
def music_page():
    s = svc()
    base = s.config.public_base(request.host)
    tab = request.args.get("tab", "songs")
    if tab not in MUSIC_TABS:
        tab = "songs"
    q = (request.args.get("q") or "").strip()
    album = (request.args.get("album") or "").strip() or None
    artist = (request.args.get("artist") or "").strip() or None
    songs: list[dict] = []
    albums: list[dict] = []
    artists: list[dict] = []
    artist_groups: list[dict] = []

    if tab == "albums":
        albums = [
            {"name": row["album"], "artist": row["artist"], "count": row["n"],
             "cover": f"/api/v1/cover/{row['cover_id']}" if row["cover_id"] else None}
            for row in s.db.albums()
        ]
    elif tab == "artists":
        groups: dict[str, list[dict]] = {}
        for row in s.db.artists():
            if q and q.casefold() not in row["artist"].casefold():
                continue
            entry = {
                "id": make_artist_id(row["artist"]),
                "name": row["artist"],
                "count": row["n"],
                "albums": row["albums"],
                "cover": f"/api/v1/cover/{row['cover_id']}" if row["cover_id"] else None,
            }
            groups.setdefault(artist_initial(row["artist"]), []).append(entry)
        artists = [artist for letter in sorted(groups, key=lambda x: (x == "#", x))
                   for artist in sorted(groups[letter], key=lambda a: a["name"].casefold())]
        artist_groups = [{"letter": letter, "artists": groups[letter]}
                         for letter in sorted(groups, key=lambda x: (x == "#", x))]
    else:
        if tab == "favorites":
            rows = s.db.favorite_rows(limit=300)
        elif tab == "recent":
            rows = s.db.recent_progress(limit=60, kind="audio")
        elif tab == "top":
            rows = s.db.top_played(limit=100)
        else:
            rows = s.db.list_items(kind="audio", q=q, album=album, artist=artist,
                                   sort="name", limit=300)
        songs = [s.library.item_payload(row, base) for row in rows]

    favorite_ids = sorted(s.db.favorite_ids())
    return render_template(
        "music.html",
        tab=tab,
        songs=songs,
        albums=albums,
        artists=artists,
        q=q,
        album=album,
        artist=artist,
        favorite_ids=favorite_ids,
        artist_groups=artist_groups,
        playlists=[dict(entry) for entry in s.db.list_playlists()],
        counts=s.db.counts(),
    )


@bp.get("/artist/<artist_key>")
def artist_page(artist_key):
    s = svc()
    base = s.config.public_base(request.host)
    name = next((candidate for candidate in s.db.artist_names()
                 if make_artist_id(candidate) == artist_key), None)
    if name is None:
        return render_template("error.html", code=404, message="歌手不存在"), 404
    rows = s.db.list_items(kind="audio", artist=name, sort="name", limit=500)
    songs = [s.library.item_payload(row, base) for row in rows]
    album_map: dict[str, dict] = {}
    cover = None
    for row in rows:
        if cover is None and row["has_cover"]:
            cover = f"/api/v1/cover/{row['id']}"
        album_name = row["album"] or ""
        if not album_name:
            continue
        entry = album_map.setdefault(
            album_name, {"name": album_name, "count": 0, "cover": None})
        entry["count"] += 1
        if entry["cover"] is None and row["has_cover"]:
            entry["cover"] = f"/api/v1/cover/{row['id']}"
    return render_template(
        "artist.html",
        artist={"id": artist_key, "name": name, "cover": cover},
        songs=songs,
        albums=sorted(album_map.values(), key=lambda item: item["name"].casefold()),
        favorite_ids=sorted(s.db.favorite_ids()),
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


@bp.get("/files")
def files_page():
    s = svc()
    kind = request.args.get("kind") or None
    if kind not in (None, "audio", "video", "image", "doc"):
        kind = None
    q = (request.args.get("q") or "").strip()
    files = s.library.list_uploads(kind, q, limit=2000)
    return render_template(
        "files.html",
        files=files,
        counts=s.library.upload_counts(),
        kind=kind,
        q=q,
        upload_max_mb=s.config.upload_max_mb,
    )


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
    next_path = request.args.get("next", "/")
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"
    one_time = request.args.get("ot")
    if one_time:
        if s.auth.consume_login_token(one_time):
            session.clear()
            session["auth"] = True
            return redirect(next_path)
        return render_template("login.html", next=next_path,
                               error="二维码已过期或已使用，请重新生成",
                               page_url=url_for("web.login", _external=True))
    if session.get("auth") is True:
        return redirect(url_for("web.index"))
    return render_template("login.html", next=next_path,
                           error=request.args.get("error"),
                           page_url=url_for("web.login", _external=True))


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


@bp.get("/connect")
def connect():
    s = svc()
    base = s.config.public_base(request.host)
    web_url = base + "/"
    manifest_url = base + "/api/v1/manifest"
    login_url = None
    expires_at = None
    if s.config.auth_enabled:
        token = s.auth.issue_login_token()
        login_url = f"{base}/login?ot={token}"
        expires_at = int(time.time()) + QR_LOGIN_TTL
    return render_template(
        "connect.html",
        web_url=web_url,
        manifest_url=manifest_url,
        login_url=login_url,
        expires_at=expires_at,
        ttl=QR_LOGIN_TTL,
    )


@bp.get("/favicon.ico")
def favicon():
    return ("", 204)
