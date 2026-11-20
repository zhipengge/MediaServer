from __future__ import annotations

from tests.conftest import write_wav


def _seed(app, config, name="Song", artist="Artist", album="Album"):
    path = write_wav(config.music_root / f"{name}.wav", title=name, artist=artist)
    from mutagen.id3 import TALB
    from mutagen.wave import WAVE

    audio = WAVE(path)
    audio.tags.add(TALB(encoding=3, text=album))
    audio.save()
    app.extensions["mediaserver"].library.scan()
    return app.test_client().get("/api/v1/items?kind=audio").get_json()["items"][0]


def test_music_page_and_play_data(app, client, config):
    _seed(app, config)
    page = client.get("/music")
    assert page.status_code == 200
    body = page.data.decode("utf-8")
    assert "播放全部" in body
    assert 'id="music-data"' in body
    assert "ms-player" in body


def test_music_scripts_load_in_dependency_order(app, client, config):
    _seed(app, config)
    body = client.get("/music").get_data(as_text=True)
    assert body.index("js/player-core.js") < body.index("js/music.js"), \
        "music.js must run after player-core.js or MSPlayer is undefined"


def test_music_page_has_exactly_one_search_box(app, client, config):
    _seed(app, config)
    body = client.get("/music").get_data(as_text=True)
    assert body.count('type="search"') == 1
    assert 'action="/music"' in body


def test_audio_library_redirects_to_music(client):
    response = client.get("/library?kind=audio")
    assert response.status_code == 302
    assert "/music" in response.headers["Location"]


def test_albums_and_artists(app, client, config):
    _seed(app, config, name="One", artist="A", album="First")
    _seed(app, config, name="Two", artist="A", album="First")
    _seed(app, config, name="Three", artist="B", album="Second")

    albums = client.get("/api/v1/albums").get_json()["albums"]
    assert {entry["name"] for entry in albums} == {"First", "Second"}
    assert next(entry for entry in albums if entry["name"] == "First")["count"] == 2

    artists = client.get("/api/v1/artists").get_json()["artists"]
    assert {entry["name"] for entry in artists} == {"A", "B"}
    assert next(entry for entry in artists if entry["name"] == "A")["count"] == 2


def test_favorites_roundtrip(app, client, config):
    item = _seed(app, config)
    assert client.get("/api/v1/favorites").get_json()["items"] == []
    added = client.put(f"/api/v1/favorites/{item['id']}")
    assert added.status_code == 200
    assert added.get_json()["favorite"] is True
    listed = client.get("/api/v1/favorites").get_json()["items"]
    assert [row["id"] for row in listed] == [item["id"]]
    removed = client.delete(f"/api/v1/favorites/{item['id']}")
    assert removed.status_code == 200
    assert client.get("/api/v1/favorites").get_json()["items"] == []


def test_favorites_rejects_unknown_item(client):
    assert client.put("/api/v1/favorites/" + "0" * 20).status_code == 400
    assert client.delete("/api/v1/favorites/not-an-id").status_code == 400


def test_listen_page_does_not_duplicate_global_player(app, client, config):
    item = _seed(app, config)
    page = client.get(f"/listen/{item['id']}").get_data(as_text=True)
    assert 'id="ms-player"' not in page
    assert 'id="player"' in page
