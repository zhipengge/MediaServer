# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [1.3.1] - 2026-11-20

### Fixed

- Music page scripts now load after `player-core.js`; previously `MSPlayer` was
  undefined when `music.js` bound its click handlers, so Play did nothing.
- Static asset URLs carry the server version to avoid stale JS/CSS after upgrades.

## [1.3.0] - 2026-11-20

### Added

- NAS-DEPLOY contract endpoints: `/api/v1/status`, `/search`, `/manifest`,
  `/media` (Range/ETag/304), `/relay` (opt-in), `/share`, `/shared`.
- SQLite index with incremental scanning, background rescans and manual trigger.
- Web client: dashboard, library, audio player with synced LRC, video player,
  image viewer, document preview, shares, playlists, settings and login.
- Video support: direct progressive playback and on-demand HLS transcoding with
  job progress, segment serving and cache eviction.
- QR connection page (`/connect`) with web URL, manifest URL, and 120-second
  single-use scan-to-login links.
- Music hub: songs / albums / artists / favorites / recently played, a global
  bottom player with queue, play modes, volume, Media Session and keyboard
  shortcuts, plus favorites and album/artist aggregation APIs.
- Metadata probing via mutagen, ffprobe and a pure-Python MP4 fallback.
- Cover extraction, thumbnails and lyrics parsing (sidecar `.lrc` and embedded).
- Share uploads (multipart) and same-origin URL shares with the `muYunShared/1` format.
- Playback progress and resume, playlists, uploads.
- Optional token authentication, signed sessions, login throttling and CSRF checks.
- Docker Compose, systemd unit and VPS reverse-tunnel deployment assets.
- Self-check script, security scan script, demo media generator and test suite.

### Security

- Path traversal and hidden-file protection on every media request.
- CORS disabled by default and restricted to an exact origin when configured.
- CSP, `X-Frame-Options`, `nosniff`, referrer policy and optional HSTS.
- SSRF-guarded relay with per-hop private-address checks.
- Upload/share size limits and filename sanitisation.

[1.3.0]: https://github.com/zhipengge/MediaServer/releases/tag/v1.3.0
