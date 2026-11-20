"""Runtime configuration for MediaServer.

Everything is environment driven so the same code runs on a laptop, in Docker,
and on a NAS.  ``MUSIC_ROOT`` / ``DATA_DIR`` keep the names used by the
NAS-DEPLOY contract so the documented docker-compose file works unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIBRARY = PROJECT_ROOT / "media"
DEFAULT_DATA = PROJECT_ROOT / "data"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off", ""}


def _env(*names: str, default: str | None = None) -> str | None:
    """First non-empty environment variable among ``names``."""
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _as_int(value: str | None, default: int, *, lo: int | None = None,
            hi: int | None = None) -> int:
    number = default if value is None or value.strip() == "" else int(value)
    if lo is not None and number < lo:
        raise ValueError(f"value {number} below minimum {lo}")
    if hi is not None and number > hi:
        raise ValueError(f"value {number} above maximum {hi}")
    return number


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


@dataclass(frozen=True)
class Config:
    """Immutable, validated server configuration."""

    library_root: Path
    data_dir: Path
    music_root: Path
    video_root: Path
    image_root: Path
    doc_root: Path
    host: str = "0.0.0.0"
    port: int = 8080
    version: str = "1.3.2"
    server_name: str = "mediaserver"
    auth_mode: str = "off"          # off | token
    auth_token: str = ""
    secret_key: str = ""
    cookie_secure: bool = False
    cors_origin: str = ""
    share_max_mb: int = 300
    upload_max_mb: int = 1024
    scan_on_start: bool = True
    scan_interval: int = 300
    thumb_cache_mb: int = 512
    hls_enabled: bool = True
    hls_height: int = 720
    hls_segment: int = 6
    hls_cache_mb: int = 8192
    hls_concurrency: int = 2
    ffmpeg: str = ""
    ffprobe: str = ""
    relay_enabled: bool = False
    relay_base: str = "https://api.injahow.cn/meting/"
    relay_timeout: int = 20
    relay_allow_private: bool = False
    search_sources: tuple[str, ...] = ("local",)
    trust_proxy: bool = False
    public_url: str = ""
    log_level: str = "INFO"
    extra: dict = field(default_factory=dict)

    # ── derived paths ────────────────────────────────────────────────
    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def shared_dir(self) -> Path:
        return self.data_dir / "shared"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def cover_cache_dir(self) -> Path:
        return self.cache_dir / "covers"

    @property
    def thumb_cache_dir(self) -> Path:
        return self.cache_dir / "thumbs"

    @property
    def hls_cache_dir(self) -> Path:
        return self.cache_dir / "hls"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "index.sqlite3"

    @property
    def mounts(self) -> dict[str, Path]:
        """URL mount name -> filesystem root (read-only library first)."""
        return {
            "music": self.music_root,
            "videos": self.video_root,
            "images": self.image_root,
            "docs": self.doc_root,
            "shared": self.shared_dir,
            "uploads": self.uploads_dir,
        }

    @property
    def auth_enabled(self) -> bool:
        return self.auth_mode == "token"

    # ── construction ─────────────────────────────────────────────────
    @classmethod
    def from_env(cls, *, root: str | None = None, data: str | None = None,
                 host: str | None = None, port: int | None = None) -> Config:
        library_root = _path(_env("MEDIA_LIBRARY_ROOT", default=str(DEFAULT_LIBRARY)))
        data_dir = _path(_env("MEDIA_DATA_DIR", "DATA_DIR", default=str(DEFAULT_DATA)))

        music_root = _path(_env("MEDIA_MUSIC_ROOT", "MUSIC_ROOT",
                                default=str(library_root / "music")))
        video_root = _path(_env("MEDIA_VIDEO_ROOT", default=str(library_root / "videos")))
        image_root = _path(_env("MEDIA_IMAGE_ROOT", default=str(library_root / "images")))
        doc_root = _path(_env("MEDIA_DOC_ROOT", default=str(library_root / "docs")))

        auth_mode = (_env("MEDIA_AUTH_MODE", default="off") or "off").lower()
        if auth_mode not in {"off", "token"}:
            raise ValueError("MEDIA_AUTH_MODE must be 'off' or 'token'")

        token = _env("MEDIA_TOKEN", default="") or ""
        if auth_mode == "token" and len(token) < 16:
            raise ValueError(
                "MEDIA_AUTH_MODE=token requires MEDIA_TOKEN with at least 16 characters"
            )

        sources = tuple(
            s.strip().lower()
            for s in (_env("MEDIA_SEARCH_SOURCES", default="local") or "local").split(",")
            if s.strip()
        )
        allowed_sources = {"local"}
        unknown = [s for s in sources if s not in allowed_sources]
        if unknown:
            raise ValueError(f"unsupported search source(s): {', '.join(unknown)}")

        secret = _env("MEDIA_SECRET_KEY", default="") or ""

        cfg = cls(
            library_root=library_root,
            data_dir=data_dir,
            music_root=music_root,
            video_root=video_root,
            image_root=image_root,
            doc_root=doc_root,
            host=host if host is not None else (_env("MEDIA_HOST", "HOST", default="0.0.0.0") or "0.0.0.0"),
            port=port if port is not None else _as_int(_env("MEDIA_PORT", "PORT"), 8080, lo=1, hi=65535),
            version="1.3.2",
            server_name=_env("MEDIA_SERVER_NAME", default="mediaserver") or "mediaserver",
            auth_mode=auth_mode,
            auth_token=token,
            secret_key=secret,
            cookie_secure=_as_bool(_env("MEDIA_COOKIE_SECURE"), False),
            cors_origin=(_env("MEDIA_CORS_ORIGIN", default="") or "").strip(),
            share_max_mb=_as_int(_env("MEDIA_SHARE_MAX_MB"), 300, lo=1, hi=100_000),
            upload_max_mb=_as_int(_env("MEDIA_UPLOAD_MAX_MB"), 1024, lo=1, hi=100_000),
            scan_on_start=_as_bool(_env("MEDIA_SCAN_ON_START"), True),
            scan_interval=_as_int(_env("MEDIA_SCAN_INTERVAL"), 300, lo=0, hi=86_400),
            thumb_cache_mb=_as_int(_env("MEDIA_THUMB_CACHE_MB"), 512, lo=8, hi=100_000),
            hls_enabled=_as_bool(_env("MEDIA_HLS_ENABLED"), True),
            hls_height=_as_int(_env("MEDIA_HLS_HEIGHT"), 720, lo=144, hi=2160),
            hls_segment=_as_int(_env("MEDIA_HLS_SEGMENT"), 6, lo=2, hi=30),
            hls_cache_mb=_as_int(_env("MEDIA_HLS_CACHE_MB"), 8192, lo=64, hi=1_000_000),
            hls_concurrency=_as_int(_env("MEDIA_HLS_CONCURRENCY"), 2, lo=1, hi=8),
            ffmpeg=_env("MEDIA_FFMPEG", default="") or "",
            ffprobe=_env("MEDIA_FFPROBE", default="") or "",
            relay_enabled=_as_bool(_env("MEDIA_RELAY_ENABLED"), False),
            relay_base=(_env("MEDIA_RELAY_BASE",
                             default="https://api.injahow.cn/meting/")
                        or "https://api.injahow.cn/meting/").rstrip("/") + "/",
            relay_timeout=_as_int(_env("MEDIA_RELAY_TIMEOUT"), 20, lo=3, hi=120),
            relay_allow_private=_as_bool(_env("MEDIA_RELAY_ALLOW_PRIVATE"), False),
            search_sources=sources,
            trust_proxy=_as_bool(_env("MEDIA_TRUST_PROXY"), False),
            public_url=(_env("MEDIA_PUBLIC_URL", default="") or "").rstrip("/"),
            log_level=(_env("MEDIA_LOG_LEVEL", default="INFO") or "INFO").upper(),
        )

        if root:
            object.__setattr__(cfg, "music_root", _path(root))
            object.__setattr__(cfg, "library_root", _path(root))
        if data:
            object.__setattr__(cfg, "data_dir", _path(data))

        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Fail fast on layouts that would leak writable data through the library."""
        if self.music_root == self.data_dir or self.music_root.is_relative_to(self.data_dir):
            raise ValueError(
                "MUSIC_ROOT must not be inside DATA_DIR (would expose shares/cache as library files)"
            )
        if self.data_dir.is_relative_to(self.music_root):
            raise ValueError("DATA_DIR must not be inside MUSIC_ROOT (library is read-only)")
        if self.auth_enabled and len(self.auth_token) < 16:
            raise ValueError("token auth requires MEDIA_TOKEN with at least 16 characters")

    # ── helpers ──────────────────────────────────────────────────────
    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.shared_dir, self.cache_dir,
                     self.cover_cache_dir, self.thumb_cache_dir, self.hls_cache_dir):
            path.mkdir(parents=True, exist_ok=True)
        for name in ("music", "videos", "images", "docs"):
            (self.uploads_dir / name).mkdir(parents=True, exist_ok=True)

    def public_base(self, request_host: str = "") -> str:
        """Absolute base URL used by the muYunImport/1 manifest."""
        if self.public_url:
            return self.public_url
        host = request_host or ""
        if not host:
            return f"http://{self.host}:{self.port}"
        if host.startswith(("http://", "https://")):
            return host.rstrip("/")
        return f"http://{host}"


def load_config(**overrides) -> Config:
    return Config.from_env(**overrides)
