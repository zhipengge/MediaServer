from __future__ import annotations

from io import BytesIO

from tests.conftest import write_wav


def _wav(config):
    return write_wav(config.music_root / "source.wav").read_bytes()


def test_folder_upload_preserves_structure(app, client, config):
    payload = _wav(config)
    response = client.post(
        "/api/v1/upload",
        data={
            "relativePath": "Album/CD1/01.wav",
            "file": (BytesIO(payload), "01.wav"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["rel"] == "uploads/music/Album/CD1/01.wav"
    assert (config.uploads_dir / "music" / "Album" / "CD1" / "01.wav").is_file()

    listing = client.get("/api/v1/files").get_json()
    assert listing["counts"]["audio"] == 1
    assert listing["files"][0]["rel"] == body["rel"]

    page = client.get("/files")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Album/CD1" in html
    assert "webkitdirectory" in html


def test_folder_upload_rejects_traversal(app, client, config):
    payload = _wav(config)
    for relative in ("../evil/01.wav", ".hidden/01.wav", "a/../../01.wav"):
        response = client.post(
            "/api/v1/upload",
            data={"relativePath": relative, "file": (BytesIO(payload), "01.wav")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 400, relative


def test_folder_upload_rejects_unknown_type(app, client, config):
    response = client.post(
        "/api/v1/upload",
        data={"relativePath": "Album/run.exe", "file": (BytesIO(b"x"), "run.exe")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 415


def test_delete_uploaded_file(app, client, config):
    payload = _wav(config)
    client.post(
        "/api/v1/upload",
        data={"relativePath": "Album/01.wav", "file": (BytesIO(payload), "01.wav")},
        content_type="multipart/form-data",
    )
    target = config.uploads_dir / "music" / "Album" / "01.wav"
    assert target.is_file()

    response = client.delete("/api/v1/files/uploads/music/Album/01.wav")
    assert response.status_code == 200
    assert not target.exists()

    assert client.delete("/api/v1/files/music/Album/01.wav").status_code == 400
    assert client.delete("/api/v1/files/uploads/music/Album/missing.wav").status_code == 404
