"""File serving with HTTP Range, conditional requests and download headers."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, abort, current_app, send_file

from .util import mime_for, resolve_rel

_CHUNK = 64 * 1024


def send_media(path: Path, *, as_attachment: bool = False,
               download_name: str | None = None,
               max_age: int = 3600) -> Response:
    """Send a file with ETag/Last-Modified and byte-range support.

    Werkzeug handles ``Range`` (206 + Content-Range), ``If-Range``, ``416`` and
    HEAD semantics once ``conditional=True`` is set; we only add a stable
    ``Accept-Ranges`` header and a conservative cache policy.
    """
    stat = path.stat()
    response = send_file(
        path,
        mimetype=mime_for(path),
        as_attachment=as_attachment,
        download_name=download_name,
        conditional=True,
        etag=True,
        last_modified=stat.st_mtime,
        max_age=max_age,
    )
    response.headers.setdefault("Accept-Ranges", "bytes")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


def lrc_response(text: str) -> Response:
    return Response(
        text,
        mimetype="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


media_bp = Blueprint("media", __name__)


@media_bp.get("/media/<path:rel>")
def serve(rel: str):
    """Serve a library file. The path is validated against the mount table."""
    services = current_app.extensions["mediaserver"]
    path = resolve_rel(services.config.mounts, rel)
    if path is None:
        abort(404)
    return send_media(path)
