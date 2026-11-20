from __future__ import annotations

from io import BytesIO

from tests.conftest import write_wav


def test_multipart_share_roundtrip(app, client, config):
    wav = write_wav(config.music_root / "seed.wav", title="Seed").read_bytes()
    response = client.post(
        "/api/v1/share",
        data={
            "kind": "file",
            "title": "Shared Song",
            "artist": "Tester",
            "from": "pytest",
            "file": (BytesIO(wav), "shared.wav"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["title"] == "Shared Song"
    assert payload["playUrl"].startswith("/media/shared/")
    assert payload["from"] == "pytest"

    listing = client.get("/api/v1/shared").get_json()
    assert listing["format"] == "muYunShared/1"
    assert len(listing["items"]) == 1
    assert listing["items"][0]["playUrl"] == payload["playUrl"]

    media = client.get(payload["playUrl"])
    assert media.status_code == 200
    assert media.headers["Content-Type"].startswith("audio/")


def test_share_rejects_empty_file(client):
    response = client.post(
        "/api/v1/share",
        data={"kind": "file", "title": "x", "file": (BytesIO(b""), "empty.mp3")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_share_rejects_missing_file(client):
    response = client.post("/api/v1/share", data={"kind": "file", "title": "x"},
                           content_type="multipart/form-data")
    assert response.status_code == 400


def test_json_share_same_origin(client, app, config):
    write_wav(config.music_root / "song.wav", title="Song")
    app.extensions["mediaserver"].library.scan()
    response = client.post("/api/v1/share", json={
        "kind": "url", "url": "/media/music/song.wav", "title": "From URL",
        "artist": "Tester", "from": "pytest",
    })
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["playUrl"] == "/media/music/song.wav"
    assert client.get(payload["playUrl"]).status_code == 200
