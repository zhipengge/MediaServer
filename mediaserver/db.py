"""SQLite index for the media library.

A single file in ``DATA_DIR`` keeps the index, playback progress, playlists and
shares.  Connections are thread-local and the database runs in WAL mode, which
is what makes concurrent Flask request threads + the background scanner safe.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    rel         TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    title       TEXT,
    artist      TEXT,
    album       TEXT,
    albumartist TEXT,
    duration    REAL,
    width       INTEGER,
    height      INTEGER,
    video_codec TEXT,
    audio_codec TEXT,
    bitrate     INTEGER,
    sample_rate INTEGER,
    has_cover   INTEGER NOT NULL DEFAULT 0,
    has_lyrics  INTEGER NOT NULL DEFAULT 0,
    lyrics_rel  TEXT,
    cover_rel   TEXT,
    meta        TEXT,
    added_at    REAL NOT NULL,
    indexed_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS items_kind ON items(kind);
CREATE INDEX IF NOT EXISTS items_title ON items(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS items_artist ON items(artist COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS items_mtime ON items(mtime);

CREATE TABLE IF NOT EXISTS progress (
    item_id     TEXT PRIMARY KEY,
    position    REAL NOT NULL DEFAULT 0,
    duration    REAL,
    updated_at  REAL NOT NULL,
    plays       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlists (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    pos         INTEGER NOT NULL,
    added_at    REAL NOT NULL,
    PRIMARY KEY (playlist_id, item_id)
);
CREATE INDEX IF NOT EXISTS playlist_pos ON playlist_items(playlist_id, pos);

CREATE TABLE IF NOT EXISTS shares (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    title       TEXT,
    artist      TEXT,
    duration    REAL,
    play_url    TEXT NOT NULL,
    cover       TEXT,
    from_name   TEXT,
    shared_at   REAL NOT NULL,
    size        INTEGER,
    mime        TEXT
);
CREATE INDEX IF NOT EXISTS shares_time ON shares(shared_at DESC);

CREATE TABLE IF NOT EXISTS favorites (
    item_id     TEXT PRIMARY KEY,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_state (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""

SORTS = {
    "recent": "COALESCE(added_at, 0) DESC, id DESC",
    "mtime": "mtime DESC, id DESC",
    "name": "COALESCE(title, rel) COLLATE NOCASE ASC",
    "title": "COALESCE(title, rel) COLLATE NOCASE ASC",
    "artist": "COALESCE(artist, '') COLLATE NOCASE ASC, COALESCE(album, '') COLLATE NOCASE ASC",
    "duration": "COALESCE(duration, 0) DESC",
    "size": "size DESC",
}


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self._write_lock = threading.Lock()

    # ── connections ──────────────────────────────────────────────────
    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=8000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def init(self) -> None:
        with self._write_lock:
            conn = self.connect()
            conn.executescript(SCHEMA)

    # ── generic helpers ─────────────────────────────────────────────
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._write_lock:
            return self.connect().execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self.connect().execute(sql, tuple(params)))

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.connect().execute(sql, tuple(params)).fetchone()
        return rows

    # ── items ───────────────────────────────────────────────────────
    def upsert_item(self, item: dict) -> None:
        item = dict(item)
        item.setdefault("indexed_at", time.time())
        item.setdefault("added_at", time.time())
        if "meta" in item and not isinstance(item["meta"], str):
            item["meta"] = json.dumps(item["meta"], ensure_ascii=False, sort_keys=True)
        columns = (
            "id", "rel", "kind", "size", "mtime", "title", "artist", "album",
            "albumartist", "duration", "width", "height", "video_codec",
            "audio_codec", "bitrate", "sample_rate", "has_cover", "has_lyrics",
            "lyrics_rel", "cover_rel", "meta", "added_at", "indexed_at",
        )
        values = tuple(item.get(c) for c in columns)
        assignments = ", ".join(f"{c}=excluded.{c}" for c in columns if c != "id")
        sql = (
            f"INSERT INTO items ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT(id) DO UPDATE SET {assignments}"
        )
        with self._write_lock:
            self.connect().execute(sql, values)

    def existing(self) -> dict[str, sqlite3.Row]:
        return {row["rel"]: row for row in self.query("SELECT * FROM items")}

    def delete_missing(self, keep: set[str]) -> int:
        removed = 0
        rows = self.query("SELECT id, rel FROM items")
        to_delete = [row["id"] for row in rows if row["rel"] not in keep]
        if not to_delete:
            return 0
        with self._write_lock:
            conn = self.connect()
            for chunk_start in range(0, len(to_delete), 200):
                chunk = to_delete[chunk_start:chunk_start + 200]
                placeholders = ", ".join("?" for _ in chunk)
                conn.execute(f"DELETE FROM items WHERE id IN ({placeholders})", chunk)
                conn.execute(f"DELETE FROM progress WHERE item_id IN ({placeholders})", chunk)
                conn.execute(f"DELETE FROM playlist_items WHERE item_id IN ({placeholders})", chunk)
                removed += len(chunk)
        return removed

    def get(self, item_id_value: str) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM items WHERE id = ?", (item_id_value,))

    def get_by_rel(self, rel: str) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM items WHERE rel = ?", (rel,))

    def counts(self) -> dict[str, int]:
        rows = self.query("SELECT kind, COUNT(*) AS n FROM items GROUP BY kind")
        result = {row["kind"]: row["n"] for row in rows}
        result["total"] = sum(result.values())
        return result

    def list_items(self, *, kind: str | None = None, q: str = "", sort: str = "recent",
                   limit: int = 50, offset: int = 0, artist: str | None = None,
                   album: str | None = None) -> list[sqlite3.Row]:
        where, params = self._where(kind=kind, q=q, artist=artist, album=album)
        order = SORTS.get(sort, SORTS["recent"])
        sql = f"SELECT * FROM items {where} ORDER BY {order} LIMIT ? OFFSET ?"
        return self.query(sql, (*params, limit, offset))

    def count_items(self, *, kind: str | None = None, q: str = "", artist: str | None = None,
                    album: str | None = None) -> int:
        where, params = self._where(kind=kind, q=q, artist=artist, album=album)
        row = self.query_one(f"SELECT COUNT(*) AS n FROM items {where}", params)
        return int(row["n"]) if row else 0

    def _where(self, *, kind: str | None, q: str, artist: str | None,
               album: str | None) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if artist:
            clauses.append("COALESCE(artist, '') = ?")
            params.append(artist)
        if album:
            clauses.append("COALESCE(album, '') = ?")
            params.append(album)
        q = (q or "").strip()
        if q:
            needle = f"%{q.lower()}%"
            clauses.append(
                "(lower(COALESCE(title,'')) LIKE ? OR lower(COALESCE(artist,'')) LIKE ? "
                "OR lower(COALESCE(album,'')) LIKE ? OR lower(rel) LIKE ?)"
            )
            params.extend([needle, needle, needle, needle])
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", params

    def facets(self, kind: str | None = None) -> dict[str, list[dict]]:
        where = "WHERE kind = ? AND COALESCE(artist,'') != ''" if kind else "WHERE COALESCE(artist,'') != ''"
        params: tuple = (kind,) if kind else ()
        artists = self.query(
            f"SELECT artist, COUNT(*) AS n FROM items {where} GROUP BY artist "
            "ORDER BY n DESC, artist COLLATE NOCASE LIMIT 100", params)
        where_album = "WHERE kind = ? AND COALESCE(album,'') != ''" if kind else "WHERE COALESCE(album,'') != ''"
        albums = self.query(
            f"SELECT album, COALESCE(artist,'') AS artist, COUNT(*) AS n FROM items {where_album} "
            "GROUP BY album ORDER BY n DESC, album COLLATE NOCASE LIMIT 100", params)
        return {
            "artists": [{"name": r["artist"], "count": r["n"]} for r in artists],
            "albums": [{"name": r["album"], "artist": r["artist"], "count": r["n"]} for r in albums],
        }

    def audio_for_manifest(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM items WHERE kind = 'audio' ORDER BY rel COLLATE NOCASE")

    # ── progress ────────────────────────────────────────────────────
    def set_progress(self, item_id_value: str, position: float, duration: float | None,
                     *, played: bool = False) -> None:
        now = time.time()
        with self._write_lock:
            conn = self.connect()
            row = conn.execute("SELECT plays FROM progress WHERE item_id = ?",
                               (item_id_value,)).fetchone()
            plays = (row["plays"] if row else 0) + (1 if played else 0)
            conn.execute(
                "INSERT INTO progress (item_id, position, duration, updated_at, plays) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(item_id) DO UPDATE SET "
                "position=excluded.position, duration=excluded.duration, "
                "updated_at=excluded.updated_at, plays=excluded.plays",
                (item_id_value, max(0.0, position), duration, now, plays))

    def get_progress(self, item_id_value: str) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM progress WHERE item_id = ?", (item_id_value,))

    def recent_progress(self, limit: int = 12, kind: str | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT i.*, p.position AS position, p.duration AS progress_duration, "
               "p.updated_at AS progress_at FROM progress p JOIN items i ON i.id = p.item_id ")
        params: list[Any] = []
        if kind:
            sql += "WHERE i.kind = ? "
            params.append(kind)
        sql += "ORDER BY p.updated_at DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    # ── playlists ───────────────────────────────────────────────────
    def list_playlists(self) -> list[sqlite3.Row]:
        return self.query(
            "SELECT p.*, COUNT(pi.item_id) AS n FROM playlists p "
            "LEFT JOIN playlist_items pi ON pi.playlist_id = p.id "
            "GROUP BY p.id ORDER BY p.created_at DESC")

    def create_playlist(self, playlist_id: str, name: str) -> None:
        self.execute("INSERT OR REPLACE INTO playlists (id, name, created_at) VALUES (?, ?, ?)",
                     (playlist_id, name, time.time()))

    def delete_playlist(self, playlist_id: str) -> None:
        with self._write_lock:
            conn = self.connect()
            conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
            conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))

    def playlist_items(self, playlist_id: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT i.*, pi.pos AS pos FROM playlist_items pi JOIN items i ON i.id = pi.item_id "
            "WHERE pi.playlist_id = ? ORDER BY pi.pos ASC", (playlist_id,))

    def add_to_playlist(self, playlist_id: str, item_id_value: str) -> None:
        with self._write_lock:
            conn = self.connect()
            row = conn.execute("SELECT COALESCE(MAX(pos), -1) AS m FROM playlist_items "
                               "WHERE playlist_id = ?", (playlist_id,)).fetchone()
            pos = (row["m"] if row else -1) + 1
            conn.execute(
                "INSERT OR IGNORE INTO playlist_items (playlist_id, item_id, pos, added_at) "
                "VALUES (?, ?, ?, ?)", (playlist_id, item_id_value, pos, time.time()))

    def remove_from_playlist(self, playlist_id: str, item_id_value: str) -> None:
        self.execute("DELETE FROM playlist_items WHERE playlist_id = ? AND item_id = ?",
                     (playlist_id, item_id_value))

    # ── shares ──────────────────────────────────────────────────────
    def add_share(self, entry: dict) -> None:
        self.execute(
            "INSERT INTO shares (id, kind, title, artist, duration, play_url, cover, "
            "from_name, shared_at, size, mime) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry["id"], entry.get("kind", "file"), entry.get("title"), entry.get("artist"),
             entry.get("duration"), entry["playUrl"], entry.get("cover"),
             entry.get("from"), entry.get("sharedAt", time.time()),
             entry.get("size"), entry.get("mime")))

    def list_shares(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM shares ORDER BY shared_at DESC LIMIT ?", (limit,))

    # ── favorites ───────────────────────────────────────────────────
    def set_favorite(self, item_id_value: str, favorite: bool) -> None:
        if favorite:
            self.execute(
                "INSERT INTO favorites (item_id, created_at) VALUES (?, ?) "
                "ON CONFLICT(item_id) DO NOTHING", (item_id_value, time.time()))
        else:
            self.execute("DELETE FROM favorites WHERE item_id = ?", (item_id_value,))

    def favorite_ids(self) -> set[str]:
        return {row["item_id"] for row in self.query("SELECT item_id FROM favorites")}

    def favorite_rows(self, limit: int = 200, offset: int = 0) -> list[sqlite3.Row]:
        return self.query(
            "SELECT i.* FROM favorites f JOIN items i ON i.id = f.item_id "
            "ORDER BY f.created_at DESC LIMIT ? OFFSET ?", (limit, offset))

    # ── album / artist aggregation ──────────────────────────────────
    def albums(self, limit: int = 300) -> list[sqlite3.Row]:
        return self.query(
            "SELECT album, COALESCE(artist, '') AS artist, COUNT(*) AS n, "
            "MAX(CASE WHEN has_cover = 1 THEN id END) AS cover_id, "
            "MAX(mtime) AS cover_mtime "
            "FROM items WHERE kind = 'audio' AND COALESCE(album, '') != '' "
            "GROUP BY album ORDER BY album COLLATE NOCASE LIMIT ?", (limit,))

    def artists(self, limit: int = 300) -> list[sqlite3.Row]:
        return self.query(
            "SELECT artist, COUNT(*) AS n, COUNT(DISTINCT album) AS albums, "
            "MAX(CASE WHEN has_cover = 1 THEN id END) AS cover_id, "
            "MAX(mtime) AS cover_mtime "
            "FROM items WHERE kind = 'audio' AND COALESCE(artist, '') != '' "
            "GROUP BY artist ORDER BY artist COLLATE NOCASE LIMIT ?", (limit,))

    def top_played(self, limit: int = 20, kind: str = "audio") -> list[sqlite3.Row]:
        return self.query(
            "SELECT i.*, p.plays AS plays FROM progress p JOIN items i ON i.id = p.item_id "
            "WHERE i.kind = ? AND p.plays > 0 ORDER BY p.plays DESC, p.updated_at DESC LIMIT ?",
            (kind, limit))

    def artist_names(self) -> list[str]:
        rows = self.query(
            "SELECT DISTINCT artist FROM items WHERE kind = 'audio' "
            "AND COALESCE(artist, '') != '' ORDER BY artist COLLATE NOCASE")
        return [row["artist"] for row in rows]

    # ── scan state ──────────────────────────────────────────────────
    def state_get(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT v FROM scan_state WHERE k = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["v"])
        except Exception:
            return default

    def state_set(self, key: str, value: Any) -> None:
        self.execute("INSERT OR REPLACE INTO scan_state (k, v) VALUES (?, ?)",
                     (key, json.dumps(value, ensure_ascii=False)))
