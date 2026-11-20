from __future__ import annotations

from mediaserver import create_app
from tests.conftest import make_config, write_wav


def test_path_traversal_rejected(client):
    for path in (
        "/media/../etc/passwd",
        "/media/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/media/music/../../etc/passwd",
        "/media/music/%2e%2e/%2e%2e/etc/passwd",
    ):
        assert client.get(path).status_code == 404, path


def test_hidden_files_are_not_served(client, config):
    hidden = config.music_root / ".env"
    hidden.write_text("SECRET=1", "utf-8")
    assert client.get("/media/music/.env").status_code == 404


def test_no_cors_headers_by_default(client):
    response = client.get("/api/v1/status", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in response.headers


def test_cors_only_for_configured_origin(tmp_path):
    config = make_config(tmp_path, cors_origin="https://web.example")
    app = create_app(config, start_scanner=False)
    client = app.test_client()
    allowed = client.get("/api/v1/status", headers={"Origin": "https://web.example"})
    assert allowed.headers["Access-Control-Allow-Origin"] == "https://web.example"
    denied = client.get("/api/v1/status", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in denied.headers
    preflight = client.options("/api/v1/status", headers={"Origin": "https://web.example"})
    assert preflight.status_code == 204


def test_security_headers(client):
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_share_url_must_be_same_origin(app, client):
    response = client.post("/api/v1/share", json={
        "kind": "url", "url": "https://evil.example/audio.mp3", "title": "x",
    })
    assert response.status_code == 400
    response = client.post("/api/v1/share", json={
        "kind": "url", "url": "http://other-host/media/music/a.mp3", "title": "x",
    })
    assert response.status_code == 400


def test_upload_rejects_unknown_extension(client):
    from io import BytesIO

    response = client.post("/api/v1/upload", data={
        "file": (BytesIO(b"payload"), "evil.exe"),
    }, content_type="multipart/form-data")
    assert response.status_code == 415


def test_upload_accepts_audio(client):
    from io import BytesIO

    wav = write_wav(client.application.extensions["mediaserver"].config.music_root /
                    "source.wav").read_bytes()
    response = client.post("/api/v1/upload", data={
        "file": (BytesIO(wav), "uploaded.wav"),
    }, content_type="multipart/form-data")
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["rel"].startswith("uploads/music/")


def test_token_mode(tmp_path):
    config = make_config(tmp_path, auth_mode="token", auth_token="t" * 32)
    app = create_app(config, start_scanner=False)
    client = app.test_client()
    assert client.get("/api/v1/status").status_code == 200
    assert client.get("/api/v1/items").status_code == 401
    assert client.get("/api/v1/items", headers={
        "Authorization": "Bearer " + "t" * 32,
    }).status_code == 200
    assert client.get("/api/v1/items", headers={"X-API-Token": "t" * 32}).status_code == 200
    assert client.get("/api/v1/items", headers={"X-API-Token": "wrong"}).status_code == 401

    login = client.post("/login", data={"token": "t" * 32, "next": "/"}, follow_redirects=False)
    assert login.status_code == 302
    assert client.get("/api/v1/items").status_code == 200

    client.post("/logout")
    assert client.get("/api/v1/items").status_code == 401


def test_weak_token_is_rejected(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        make_config(tmp_path, auth_mode="token", auth_token="short").validate()
