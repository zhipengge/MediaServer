"""MediaServer application factory."""

from __future__ import annotations

import atexit
import logging
import time
from types import SimpleNamespace

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from .api import bp as api_bp
from .auth import AuthService
from .config import Config
from .db import Database
from .library import LibraryManager
from .media import media_bp
from .share import ShareService
from .util import duration_label, human_size
from .video import VideoService
from .web import bp as web_bp

log = logging.getLogger("mediaserver")


def create_app(config: Config | None = None, *, start_scanner: bool = True) -> Flask:
    cfg = config or Config.from_env()
    cfg.ensure_dirs()

    db = Database(cfg.db_path)
    db.init()
    library = LibraryManager(cfg, db)
    video = VideoService(cfg, library)
    share = ShareService(cfg, db, library.tools)
    auth = AuthService(cfg, cfg.data_dir / ".secret")

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.update(
        SECRET_KEY=auth.secret_key,
        MAX_CONTENT_LENGTH=max(cfg.upload_max_mb, cfg.share_max_mb) * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.cookie_secure,
        JSON_SORT_KEYS=False,
        TEMPLATES_AUTO_RELOAD=cfg.log_level == "DEBUG",
        PREFERRED_URL_SCHEME="https" if cfg.cookie_secure else "http",
    )

    services = SimpleNamespace(
        config=cfg, db=db, library=library, video=video, share=share, auth=auth,
    )
    app.extensions["mediaserver"] = services

    app.register_blueprint(api_bp)
    app.register_blueprint(web_bp)
    app.register_blueprint(media_bp)

    # ── authentication ───────────────────────────────────────────────
    @app.before_request
    def _require_auth():
        if request.method == "OPTIONS":
            return None
        if services.auth.authorized(request):
            return None
        if request.path.startswith("/api/"):
            response = jsonify({"error": "unauthorized",
                                "hint": "send Authorization: Bearer <MEDIA_TOKEN>"})
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        next_path = request.full_path if request.query_string else request.path
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/"
        return redirect(url_for("web.login", next=next_path))

    # ── CSRF: state-changing requests authenticated by cookie only ──
    @app.before_request
    def _csrf_guard():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        if not services.config.auth_enabled or session.get("auth") is not True:
            return None
        host = request.host_url.rstrip("/")
        origin = request.headers.get("Origin")
        referer = request.headers.get("Referer")
        if origin and origin.rstrip("/") != host:
            return jsonify({"error": "cross-origin request rejected"}), 403
        if not origin and referer and not referer.startswith(host + "/"):
            return jsonify({"error": "cross-origin request rejected"}), 403
        return None

    # ── CORS (opt-in, exact origin only) ────────────────────────────
    @app.before_request
    def _cors_preflight():
        origin = services.config.cors_origin
        if request.method == "OPTIONS" and origin and request.path.startswith("/api/"):
            if request.headers.get("Origin") == origin:
                return ("", 204)
            return ("", 403)
        return None

    # ── response hardening ──────────────────────────────────────────
    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.headers.setdefault("X-MediaServer-Version", services.config.version)
        if response.mimetype == "text/html":
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; media-src 'self'; "
                "script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; "
                "form-action 'self'")
        origin = services.config.cors_origin
        if origin and request.headers.get("Origin") == origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, Range, X-API-Token")
            response.headers["Access-Control-Max-Age"] = "600"
        if services.config.cookie_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=86400; includeSubDomains")
        return response

    # ── errors ──────────────────────────────────────────────────────
    @app.errorhandler(404)
    def _not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return render_template("error.html", code=404, message="页面不存在"), 404

    @app.errorhandler(413)
    def _too_large(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "request body too large"}), 413
        return render_template("error.html", code=413, message="请求体超过上限"), 413

    @app.errorhandler(500)
    def _server_error(error):
        log.exception("unhandled error: %s", error)
        if request.path.startswith("/api/"):
            return jsonify({"error": "internal server error"}), 500
        return render_template("error.html", code=500, message="服务器内部错误"), 500

    # ── template helpers ───────────────────────────────────────────
    app.jinja_env.filters["fmt_size"] = human_size
    app.jinja_env.filters["fmt_duration"] = duration_label
    app.jinja_env.filters["fmt_time"] = (
        lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
        if ts else "")

    @app.context_processor
    def _inject():
        return {
            "server": {
                "name": services.config.server_name,
                "version": services.config.version,
                "auth": services.config.auth_enabled,
                "hls": services.video.enabled,
                "relay": services.config.relay_enabled,
            },
            "counts": db.counts(),
            "kinds": {"audio": "音乐", "video": "视频", "image": "图片", "doc": "文档"},
        }

    if start_scanner:
        library.start()
        atexit.register(library.stop)
        atexit.register(video.shutdown)
        atexit.register(db.close)

    return app
