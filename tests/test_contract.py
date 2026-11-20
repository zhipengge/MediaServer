from __future__ import annotations

from urllib.parse import urlparse

from tests.conftest import write_wav


def scan(app):
    return app.extensions["mediaserver"].library.scan()


def test_status_contract(client):
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["gatewayVersion"]
    assert "counts" in payload
    assert payload["share"]["format"] == "muYunShared/1"


def test_manifest_contract(app, client, config):
    write_wav(config.music_root / "Album" / "01 - Test Song.wav", title="Test Song",
              artist="Tester", lyrics="[00:00.00]hello\n")
    scan(app)
    response = client.get("/api/v1/manifest")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["schema"] == "muYunImport/1"
    tracks = payload["tracks"]
    assert len(tracks) == 1
    track = tracks[0]
    assert track["title"] == "Test Song"
    assert track["artist"] == "Tester"
    parsed = urlparse(track["url"])
    assert parsed.path.startswith("/media/music/")
    assert track["url"].startswith("http://localhost/")
    assert track["duration"] and track["duration"] > 1
    assert track["tags"]


def test_search_contract(app, client, config):
    write_wav(config.music_root / "Track.wav", title="Searchable Tune", artist="A",
              lyrics="[00:00.00]line\n")
    scan(app)
    response = client.get("/api/v1/search?q=searchable")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["format"] == "muYunSearch/1"
    assert payload["sources"] == ["local"]
    assert len(payload["results"]) == 1
    row = payload["results"][0]
    assert row["title"] == "Searchable Tune"
    assert row["playUrl"].startswith("/media/music/")
    assert row["lyricsUrl"].startswith("/api/v1/lyrics/")
    assert row["download"].startswith("/api/v1/download?")
    assert row["source"] == "local"


def test_search_requires_query(client):
    response = client.get("/api/v1/search")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_search_unknown_source(client):
    response = client.get("/api/v1/search?q=x&sources=spotify")
    assert response.status_code == 400


def test_shared_contract(client):
    response = client.get("/api/v1/shared")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["format"] == "muYunShared/1"
    assert payload["items"] == []


def test_library_and_items(app, client, config):
    write_wav(config.music_root / "One.wav", title="One")
    scan(app)
    library = client.get("/api/v1/library").get_json()
    assert library["sections"]["audio"]["count"] == 1
    items = client.get("/api/v1/items?kind=audio").get_json()
    assert items["total"] == 1
    item = items["items"][0]
    detail = client.get(f"/api/v1/items/{item['id']}")
    assert detail.status_code == 200
    assert detail.get_json()["title"] == "One"
