from __future__ import annotations

import dataclasses
import math
import struct
import wave
from pathlib import Path

import pytest

from mediaserver import create_app
from mediaserver.config import Config


def make_config(tmp_path: Path, **overrides) -> Config:
    library = tmp_path / "media"
    data = tmp_path / "data"
    cfg = Config(
        library_root=library,
        data_dir=data,
        music_root=library / "music",
        video_root=library / "videos",
        image_root=library / "images",
        doc_root=library / "docs",
        host="127.0.0.1",
        port=8080,
        scan_on_start=False,
        scan_interval=0,
        hls_enabled=False,
    )
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    cfg.ensure_dirs()
    for directory in (cfg.music_root, cfg.video_root, cfg.image_root, cfg.doc_root):
        directory.mkdir(parents=True, exist_ok=True)
    return cfg


def write_wav(path: Path, seconds: float = 2.0, frequency: float = 440.0,
              title: str = "", artist: str = "", lyrics: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(22050 * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        data = bytearray()
        for i in range(frames):
            envelope = min(1.0, i / 2000, (frames - i) / 2000)
            sample = int(6000 * envelope * math.sin(2 * math.pi * frequency * i / 22050))
            data += struct.pack("<h", sample)
        handle.writeframes(bytes(data))
    if title:
        from mutagen.id3 import TALB, TIT2, TPE1, USLT
        from mutagen.wave import WAVE

        audio = WAVE(path)
        audio.add_tags()
        audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TPE1(encoding=3, text=artist or "Test Artist"))
        audio.tags.add(TALB(encoding=3, text="Test Album"))
        if lyrics:
            audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
        audio.save()
    return path


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    return make_config(tmp_path)


@pytest.fixture()
def app(config: Config):
    application = create_app(config, start_scanner=False)
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def services(app):
    return app.extensions["mediaserver"]
