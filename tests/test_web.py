from __future__ import annotations

from mediaserver import create_app
from tests.conftest import make_config, write_wav


def test_home_page(app, client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["Content-Type"]
    body = response.data.decode("utf-8")
    assert "家里的影音库" in body
    assert "/static/css/app.css" in body


def test_library_and_settings_pages(app, client, config):
    write_wav(config.music_root / "song.wav", title="Song")
    app.extensions["mediaserver"].library.scan()
    library = client.get("/library")
    assert library.status_code == 200
    assert "Song" in library.data.decode("utf-8")
    settings = client.get("/settings")
    assert settings.status_code == 200
    assert "服务器状态" in settings.data.decode("utf-8")
    shares = client.get("/shares")
    assert shares.status_code == 200


def test_listen_page(app, client, config):
    write_wav(config.music_root / "song.wav", title="Song", lyrics="[00:00.00]hi\n")
    app.extensions["mediaserver"].library.scan()
    item = client.get("/api/v1/items").get_json()["items"][0]
    page = client.get(f"/listen/{item['id']}")
    assert page.status_code == 200
    body = page.data.decode("utf-8")
    assert "player-data" in body
    assert "Song" in body


def test_missing_page_404(client):
    assert client.get("/listen/does-not-exist").status_code == 404


def test_login_required(tmp_path):
    config = make_config(tmp_path, auth_mode="token", auth_token="s" * 32)
    app = create_app(config, start_scanner=False)
    client = app.test_client()
    response = client.get("/library")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    page = client.get("/login")
    assert page.status_code == 200
    assert "需要口令" in page.data.decode("utf-8")
