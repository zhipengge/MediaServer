from __future__ import annotations

from tests.conftest import write_wav


def test_full_get_and_range(app, client, config):
    write_wav(config.music_root / "song.wav", title="Song")
    app.extensions["mediaserver"].library.scan()
    url = "/media/music/song.wav"
    full = client.get(url)
    assert full.status_code == 200
    assert full.headers["Content-Type"].startswith("audio/")
    assert full.headers["Accept-Ranges"] == "bytes"
    total = len(full.data)

    partial = client.get(url, headers={"Range": "bytes=10-19"})
    assert partial.status_code == 206
    assert partial.headers["Content-Range"] == f"bytes 10-19/{total}"
    assert len(partial.data) == 10

    head = client.head(url)
    assert head.status_code == 200
    assert int(head.headers["Content-Length"]) == total


def test_etag_304(app, client, config):
    write_wav(config.music_root / "song.wav")
    app.extensions["mediaserver"].library.scan()
    url = "/media/music/song.wav"
    first = client.get(url)
    etag = first.headers.get("ETag")
    assert etag
    second = client.get(url, headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_bad_range_416(app, client, config):
    write_wav(config.music_root / "song.wav")
    app.extensions["mediaserver"].library.scan()
    response = client.get("/media/music/song.wav", headers={"Range": "bytes=999999-"})
    assert response.status_code == 416


def test_missing_media_404(client):
    assert client.get("/media/music/nope.mp3").status_code == 404
    assert client.get("/media/unknown/x.mp3").status_code == 404


def test_download_attachment(app, client, config):
    write_wav(config.music_root / "song.wav", title="Song")
    app.extensions["mediaserver"].library.scan()
    item = client.get("/api/v1/items").get_json()["items"][0]
    response = client.get(f"/api/v1/download?item={item['id']}")
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]


def test_cover_and_lyrics(app, client, config):
    write_wav(config.music_root / "song.wav", title="Song",
              lyrics="[00:00.00]第一行\n[00:01.00]第二行\n")
    app.extensions["mediaserver"].library.scan()
    item = client.get("/api/v1/items").get_json()["items"][0]
    assert item["hasLyrics"] is True
    lyrics = client.get(f"/api/v1/lyrics/{item['id']}")
    assert lyrics.status_code == 200
    assert "第一行" in lyrics.data.decode("utf-8")
    parsed = client.get(f"/api/v1/lyrics/{item['id']}?format=json").get_json()
    assert parsed["synced"] is True
    assert len(parsed["lines"]) == 2
    assert parsed["lines"][1]["time"] == 1.0
