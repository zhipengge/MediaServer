#!/usr/bin/env python3
"""Generate a small, license-clean demo library.

Everything is synthesised locally (sine waves, Pillow gradients, ffmpeg testsrc),
so the repository never has to ship third-party music or video.

    pipenv run python scripts/seed_demo.py            # into ./media
    pipenv run python scripts/seed_demo.py --force    # regenerate
    pipenv run python scripts/seed_demo.py --root /tmp/demo
"""

from __future__ import annotations

import argparse
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def ffmpeg_bin() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def cover_art(path: Path, title: str, seed: int) -> None:
    size = 640
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    palette = [
        ((20, 34, 46), (46, 196, 168)),
        ((38, 28, 54), (176, 132, 255)),
        ((48, 32, 26), (255, 178, 108)),
    ]
    start, end = palette[seed % len(palette)]
    for y in range(size):
        for x in range(0, size, 2):
            ratio = (x + y) / (2 * size)
            wave = 0.12 * math.sin((x + seed * 40) / 34) * math.cos((y + seed * 25) / 41)
            ratio = min(1.0, max(0.0, ratio + wave))
            color = tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3))
            pixels[x, y] = color
            if x + 1 < size:
                pixels[x + 1, y] = color
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 44)
        small = ImageFont.truetype("DejaVuSans.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
        small = font
    draw.text((48, size - 132), title, fill=(255, 255, 255), font=font)
    draw.text((48, size - 72), "MediaServer demo", fill=(230, 240, 240), font=small)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=88)


def make_wav(path: Path, seconds: float, frequency: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(44100 * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        data = bytearray()
        for i in range(frames):
            envelope = min(1.0, i / 4410, (frames - i) / 4410)
            value = int(11000 * envelope * math.sin(2 * math.pi * frequency * i / 44100))
            data += struct.pack("<h", value)
        handle.writeframes(bytes(data))


def tag_with_mutagen(path: Path, meta: dict, cover: Path | None) -> None:
    from mutagen.id3 import APIC, TALB, TCON, TIT2, TPE1, USLT
    from mutagen.wave import WAVE

    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=meta["title"]))
    audio.tags.add(TPE1(encoding=3, text=meta["artist"]))
    audio.tags.add(TALB(encoding=3, text=meta["album"]))
    audio.tags.add(TCON(encoding=3, text="Demo"))
    if meta.get("lyrics"):
        audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=meta["lyrics"]))
    if cover is not None and cover.is_file():
        audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover",
                            data=cover.read_bytes()))
    audio.save()


TRACKS = [
    {
        "filename": "01 - Morning Light.mp3",
        "title": "Morning Light",
        "artist": "Demo Ensemble",
        "album": "Synthetic Sessions",
        "frequency": 440.0,
        "seconds": 12.0,
        "lyrics": "[00:00.00]Morning light over the rack\n[00:03.00]Fans are humming, disks are warm\n"
                  "[00:06.00]Every packet finds its way\n[00:09.00]Home before the evening rain\n",
        "lrc": True,
    },
    {
        "filename": "02 - 静夜思.mp3",
        "title": "静夜思",
        "artist": "演示歌手",
        "album": "Synthetic Sessions",
        "frequency": 523.25,
        "seconds": 10.0,
        "lyrics": "[00:00.00]床前明月光\n[00:02.50]疑是地上霜\n[00:05.00]举头望明月\n"
                  "[00:07.50]低头思故乡\n",
        "lrc": False,
    },
    {
        "filename": "03 - Slow Pulse.mp3",
        "title": "Slow Pulse",
        "artist": "Demo Ensemble",
        "album": "Synthetic Sessions",
        "frequency": 329.63,
        "seconds": 14.0,
        "lyrics": "",
        "lrc": False,
    },
]


def build_audio(root: Path, force: bool) -> int:
    music = root / "album" / "Synthetic Sessions"
    music.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_bin()
    created = 0
    for index, track in enumerate(TRACKS):
        target = music / track["filename"]
        if target.exists() and not force:
            continue
        cover = music / f"cover-{index}.jpg"
        cover_art(cover, track["title"] if track["title"].isascii() else f"Track {index + 1}", index)
        produced = False
        if ffmpeg:
            wav = music / f".{track['filename']}.wav"
            make_wav(wav, track["seconds"], track["frequency"])
            command = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi",
                "-i", f"sine=frequency={track['frequency']}:duration={track['seconds']}:sample_rate=44100",
                "-i", str(cover),
                "-map", "0:a", "-map", "1:v",
                "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100",
                "-c:v", "mjpeg", "-id3v2_version", "3",
                "-metadata", f"title={track['title']}",
                "-metadata", f"artist={track['artist']}",
                "-metadata", f"album={track['album']}",
                "-metadata", "genre=Demo",
            ]
            if track["lyrics"]:
                command += ["-metadata", f"lyrics={track['lyrics']}"]
            command.append(str(target))
            proc = subprocess.run(command, capture_output=True)
            wav.unlink(missing_ok=True)
            produced = proc.returncode == 0 and target.is_file()
            if not produced:
                print(f"  ! ffmpeg failed for {track['filename']}: "
                      f"{proc.stderr.decode('utf-8', 'replace')[-300:]}")
        if not produced:
            target = music / (Path(track["filename"]).stem + ".wav")
            make_wav(target, track["seconds"], track["frequency"])
            tag_with_mutagen(target, track, cover)
        if track["lrc"] and track["lyrics"]:
            lrc = target.with_suffix(".lrc")
            lrc.write_text(track["lyrics"], "utf-8")
        created += 1
        print(f"  + {target.relative_to(root)}")
    # A cover for the album directory (used by the manifest as fallback).
    album_cover = music / "cover.jpg"
    if not album_cover.exists() or force:
        cover_art(album_cover, "Synthetic Sessions", 0)
    return created


def build_images(root: Path, force: bool) -> int:
    directory = root / "images"
    directory.mkdir(parents=True, exist_ok=True)
    created = 0
    for index in range(3):
        target = directory / f"abstract-{index + 1}.png"
        if target.exists() and not force:
            continue
        width, height = 1600, 1000
        image = Image.new("RGB", (width, height))
        pixels = image.load()
        for y in range(0, height, 2):
            for x in range(0, width, 2):
                r = int(80 + 120 * math.sin((x / width) * 3.1 + index))
                g = int(90 + 100 * math.cos((y / height) * 2.6 + index * 0.7))
                b = int(120 + 90 * math.sin((x + y) / 420 + index))
                for dy in range(2):
                    for dx in range(2):
                        if x + dx < width and y + dy < height:
                            pixels[x + dx, y + dy] = (r, g, b)
        draw = ImageDraw.Draw(image)
        draw.text((40, height - 60), f"MediaServer demo image {index + 1}", fill=(245, 245, 245))
        image.save(target, "PNG")
        created += 1
        print(f"  + {target.relative_to(root)}")
    return created


def build_docs(root: Path, force: bool) -> int:
    directory = root / "docs"
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "README.md": "# Demo library\n\n这些文件由 `scripts/seed_demo.py` 生成，"
                     "用于验证索引、搜索、预览与分享。\n\n- 音乐：合成正弦波 + ID3 标签\n"
                     "- 图片：Pillow 渐变\n- 视频：ffmpeg testsrc（如果可用）\n",
        "poem.txt": "静夜思\n\n床前明月光，\n疑是地上霜。\n举头望明月，\n低头思故乡。\n",
        "licenses.txt": "All demo assets are generated locally; no third-party media is bundled.\n",
    }
    created = 0
    for name, content in files.items():
        target = directory / name
        if target.exists() and not force:
            continue
        target.write_text(content, "utf-8")
        created += 1
        print(f"  + {target.relative_to(root)}")
    return created


def build_videos(root: Path, force: bool) -> int:
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        print("  · 跳过视频：未找到 ffmpeg")
        return 0
    directory = root / "videos"
    directory.mkdir(parents=True, exist_ok=True)
    videos = [
        ("synthetic-720p.mp4", "1280x720", 330.0, "Demo Clip 720p"),
        ("synthetic-360p.mp4", "640x360", 220.0, "Demo Clip 360p"),
    ]
    created = 0
    for name, size, frequency, title in videos:
        target = directory / name
        if target.exists() and not force:
            continue
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration=6",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=6",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-shortest",
            "-metadata", f"title={title}",
            str(target),
        ]
        proc = subprocess.run(command, capture_output=True)
        if proc.returncode == 0 and target.is_file():
            created += 1
            print(f"  + {target.relative_to(root)}")
        else:
            print(f"  ! 视频生成失败（缺少 libx264？）：{proc.stderr.decode('utf-8', 'replace')[-200:]}")
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a synthetic demo media library")
    parser.add_argument("--root", default="media", help="library root (default: ./media)")
    parser.add_argument("--force", action="store_true", help="regenerate existing files")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    (root / "music").mkdir(parents=True, exist_ok=True)
    print(f"生成演示媒体到 {root}")
    total = 0
    total += build_audio(root / "music", args.force)
    total += build_images(root, args.force)
    total += build_docs(root, args.force)
    total += build_videos(root, args.force)
    print(f"完成：{total} 个新文件。启动后可在设置页触发扫描。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
