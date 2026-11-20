#!/usr/bin/env python3
"""End-to-end acceptance checks against a running MediaServer.

Standard library only, so it can run from any machine on the LAN:

    python3 scripts/selfcheck.py --base http://192.0.2.10:8080
    python3 scripts/selfcheck.py --base http://127.0.0.1:8080 --token "$MEDIA_TOKEN"
    python3 scripts/selfcheck.py --base http://127.0.0.1:8080 --video --share-file song.mp3

Exit code 0 = all checks passed (warnings are allowed), 1 = at least one failure.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

TIMEOUT = 15.0
PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    mark = {PASS: "✓", FAIL: "✗", WARN: "!"}[status]
    line = f"  {mark} [{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)


class Client:
    def __init__(self, base: str, token: str = ""):
        self.base = base.rstrip("/") + "/"
        self.token = token

    def request(self, path: str, *, method: str = "GET", headers: dict | None = None,
                body: bytes | None = None, timeout: float = TIMEOUT):
        url = urljoin(self.base, path.lstrip("/"))
        sent = dict(headers or {})
        if self.token:
            sent.setdefault("Authorization", f"Bearer {self.token}")
        req = Request(url, data=body, method=method, headers=sent)
        try:
            with urlopen(req, timeout=timeout) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as exc:
            data = exc.read()
            return exc.code, dict(exc.headers or {}), data
        except URLError as exc:
            raise SystemExit(f"无法连接 {url}: {exc.reason}") from exc

    def json(self, path: str, **kwargs):
        status, headers, body = self.request(path, **kwargs)
        try:
            return status, headers, json.loads(body.decode("utf-8", "replace"))
        except Exception:
            return status, headers, None


def same_origin(url: str, base: str) -> bool:
    if url.startswith("/"):
        return True
    target = urlparse(url)
    origin = urlparse(base)
    return (target.scheme, target.netloc) == (origin.scheme, origin.netloc)


def check_status(client: Client) -> dict:
    status, _headers, payload = client.json("/api/v1/status")
    if status != 200 or not isinstance(payload, dict):
        record(FAIL, "status 返回 JSON", f"HTTP {status}")
        return {}
    if not (payload.get("ok") is True or payload.get("gatewayVersion")):
        record(FAIL, "status 是自家网关", "既无 ok:true 也无 gatewayVersion")
    else:
        record(PASS, "status 是自家网关", f"version={payload.get('gatewayVersion')}")
    if not payload.get("gatewayVersion"):
        record(WARN, "status 带版本号", "老版本网关没有 gatewayVersion")
    return payload


def check_manifest(client: Client, base: str) -> list:
    status, _, payload = client.json("/api/v1/manifest")
    if status != 200 or not isinstance(payload, dict):
        record(FAIL, "manifest 返回 JSON", f"HTTP {status}")
        return []
    if payload.get("schema") != "muYunImport/1":
        record(FAIL, "manifest schema", str(payload.get("schema")))
    else:
        record(PASS, "manifest schema", "muYunImport/1")
    tracks = payload.get("tracks") or []
    if not tracks:
        record(WARN, "manifest 非空", "曲库为空（先放音乐并扫描）")
        return []
    record(PASS, "manifest 非空", f"{len(tracks)} 首")
    bad = [t for t in tracks if not t.get("url") or not same_origin(t["url"], base)]
    if bad:
        record(FAIL, "manifest URL 同源", f"{len(bad)} 条越界，例如 {bad[0].get('url')}")
    else:
        record(PASS, "manifest URL 同源", "全部指向本服务器")
    missing = [t for t in tracks[:5] if not t.get("title")]
    if missing:
        record(WARN, "manifest 标题", "部分条目没有 title")
    return tracks


def check_search(client: Client, base: str, query: str) -> dict | None:
    status, _, payload = client.json(f"/api/v1/search?q={quote(query)}&limit=10")
    if status != 200 or not isinstance(payload, dict):
        record(FAIL, "search 返回 JSON", f"HTTP {status}")
        return None
    if payload.get("format") != "muYunSearch/1":
        record(FAIL, "search format", str(payload.get("format")))
    else:
        record(PASS, "search format", "muYunSearch/1")
    rows = payload.get("results") or []
    if not rows:
        record(WARN, "search 有结果", f"关键词 {query!r} 没有命中")
    else:
        record(PASS, "search 有结果", f"{len(rows)} 条")
    bad = [row for row in rows
           if not (row.get("playUrl") or row.get("url"))
           or not same_origin(row.get("playUrl") or row.get("url"), base)]
    if bad:
        record(FAIL, "search 播放地址同源", f"{len(bad)} 条越界")
    else:
        record(PASS, "search 播放地址同源")
    return rows[0] if rows else None


def check_range(client: Client, row: dict | None, tracks: list) -> None:
    candidate = None
    if row and row.get("playUrl"):
        candidate = row["playUrl"]
    elif tracks:
        candidate = tracks[0]["url"]
    if not candidate:
        record(WARN, "Range 支持", "没有可测试的媒体")
        return
    path = candidate
    if candidate.startswith("http"):
        parsed = urlparse(candidate)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    status, headers, _ = client.request(path, method="HEAD",
                                       headers={"Range": "bytes=0-1023"})
    lowered = {key.lower(): value for key, value in headers.items()}
    if status == 206 and lowered.get("content-range") and lowered.get("accept-ranges"):
        record(PASS, "Range 返回 206", lowered.get("content-range"))
    elif status == 200 and lowered.get("accept-ranges"):
        record(WARN, "Range 返回 206", "服务端接受 Range 但本次没切到 206")
    else:
        record(FAIL, "Range 返回 206", f"HTTP {status}")
    status, headers, _ = client.request(path, method="HEAD")
    lowered = {key.lower(): value for key, value in headers.items()}
    if status == 200 and lowered.get("content-length") and lowered.get("content-type"):
        record(PASS, "HEAD 头部完整", f"{lowered.get('content-type')} {lowered.get('content-length')}B")
    else:
        record(FAIL, "HEAD 头部完整", f"HTTP {status}")


def check_traversal(client: Client) -> None:
    variants = [
        "/media/../etc/passwd",
        "/media/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/media/music/../../../etc/passwd",
        "/media/.hidden",
    ]
    failures = []
    for path in variants:
        code, _, _ = client.request(path)
        if code not in (400, 403, 404):
            failures.append(f"{path} -> {code}")
    if failures:
        record(FAIL, "路径穿越防护", "; ".join(failures))
    else:
        record(PASS, "路径穿越防护", "全部 404")


def check_cors(client: Client) -> None:
    _status, headers, _ = client.request("/api/v1/status")
    lowered = {key.lower(): value for key, value in headers.items()}
    if "access-control-allow-origin" in lowered:
        record(WARN, "默认不发 CORS", f"Access-Control-Allow-Origin={lowered['access-control-allow-origin']}")
    else:
        record(PASS, "默认不发 CORS")


def check_shared(client: Client) -> None:
    status, _, payload = client.json("/api/v1/shared")
    if status == 200 and isinstance(payload, dict) and payload.get("format") == "muYunShared/1":
        record(PASS, "shared 契约", f"{len(payload.get('items') or [])} 条分享")
    else:
        record(FAIL, "shared 契约", f"HTTP {status} / {payload}")


def check_web(client: Client) -> None:
    status, headers, body = client.request("/")
    lowered = {key.lower(): value for key, value in headers.items()}
    if status == 200 and "text/html" in lowered.get("content-type", ""):
        record(PASS, "网页客户端", f"{len(body)} 字节 HTML")
    else:
        record(FAIL, "网页客户端", f"HTTP {status}")


def check_items(client: Client) -> dict | None:
    status, _, payload = client.json("/api/v1/items?limit=5")
    if status != 200 or not isinstance(payload, dict):
        record(FAIL, "items 扩展接口", f"HTTP {status}")
        return None
    items = payload.get("items") or []
    record(PASS, "items 扩展接口", f"total={payload.get('total')}")
    return items[0] if items else None


def check_artwork(client: Client, item: dict | None) -> None:
    if not item:
        record(WARN, "封面 / 缩略图", "没有条目可测")
        return
    if item.get("hasCover"):
        status, headers, _ = client.request(f"/api/v1/cover/{item['id']}")
        lowered = {key.lower(): value for key, value in headers.items()}
        if status == 200 and lowered.get("content-type", "").startswith("image/"):
            record(PASS, "封面接口", lowered.get("content-type"))
        else:
            record(FAIL, "封面接口", f"HTTP {status}")
    if item.get("hasLyrics"):
        status, _, body = client.request(f"/api/v1/lyrics/{item['id']}")
        if status == 200 and body:
            record(PASS, "歌词接口", f"{len(body)} 字节")
        else:
            record(FAIL, "歌词接口", f"HTTP {status}")


def check_video(client: Client, status_payload: dict) -> None:
    features = status_payload.get("features") or {}
    if not features.get("hls"):
        record(WARN, "HLS 转码", "服务器未启用 HLS（缺 ffmpeg 或 MEDIA_HLS_ENABLED=0）")
        return
    status, _, payload = client.json("/api/v1/items?kind=video&limit=1")
    items = (payload or {}).get("items") or []
    if not items:
        record(WARN, "HLS 转码", "曲库里没有视频")
        return
    item = items[0]
    status, _, job = client.json(f"/api/v1/transcode/{item['id']}", method="POST",
                                 headers={"Content-Type": "application/json"}, body=b"{}")
    if status not in (200, 202) or not isinstance(job, dict) or not job.get("jobId"):
        record(FAIL, "提交转码任务", f"HTTP {status} {job}")
        return
    job_id = job["jobId"]
    record(PASS, "提交转码任务", job_id)
    deadline = time.time() + 240
    state = job
    while time.time() < deadline:
        state = client.json(f"/api/v1/jobs/{job_id}")[2] or {}
        if state.get("status") in ("ready", "error"):
            break
        time.sleep(2)
    if state.get("status") != "ready":
        record(FAIL, "转码完成", f"status={state.get('status')} {state.get('error') or ''}")
        return
    record(PASS, "转码完成", f"{state.get('progress')}")
    status, headers, body = client.request(f"/api/v1/hls/{job_id}/index.m3u8")
    if status == 200 and b"#EXTM3U" in body and b"#EXTINF" in body:
        record(PASS, "HLS 播放列表", f"{len(body)} 字节")
    else:
        record(FAIL, "HLS 播放列表", f"HTTP {status}")
        return
    imports = [line for line in body.decode("utf-8", "replace").splitlines()
               if line and not line.startswith("#")]
    if imports:
        seg_path = f"/api/v1/hls/{job_id}/{imports[0]}"
        status, headers, _ = client.request(seg_path)
        if status == 200 and headers.get("Content-Type") == "video/mp2t":
            record(PASS, "HLS 分片", imports[0])
        else:
            record(FAIL, "HLS 分片", f"HTTP {status}")


def check_share_upload(client: Client, filename: str) -> None:
    path = os.path.abspath(filename)
    if not os.path.isfile(path):
        record(FAIL, "分享上传", f"文件不存在 {path}")
        return
    boundary = "----mediaserver" + uuid.uuid4().hex
    with open(path, "rb") as handle:
        content = handle.read()
    name = os.path.basename(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nfile\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nselfcheck demo\r\n".encode(),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
         f"Content-Type: {mime}\r\n\r\n").encode() + content + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    status, _, payload = client.json("/api/v1/share", method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                                     body=body)
    if status == 201 and isinstance(payload, dict) and payload.get("playUrl"):
        record(PASS, "分享上传", payload["playUrl"])
        status, _, _ = client.request(payload["playUrl"], method="HEAD")
        if status == 200:
            record(PASS, "分享可播放", payload["playUrl"])
        else:
            record(FAIL, "分享可播放", f"HTTP {status}")
    else:
        record(FAIL, "分享上传", f"HTTP {status} {payload}")


def main() -> int:
    parser = argparse.ArgumentParser(description="MediaServer acceptance checks")
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--token", default=os.environ.get("MEDIA_TOKEN", ""))
    parser.add_argument("--q", default="", help="search keyword (defaults to the first track title)")
    parser.add_argument("--share-file", help="also POST this file to /api/v1/share")
    parser.add_argument("--video", action="store_true", help="also run an HLS transcoding round-trip")
    args = parser.parse_args()

    client = Client(args.base, args.token)
    print(f"MediaServer self-check → {args.base}")
    status_payload = check_status(client)
    tracks = check_manifest(client, args.base)
    query = args.q or (tracks[0].get("title") if tracks and tracks[0].get("title") else "a")
    row = check_search(client, args.base, query)
    check_range(client, row, tracks)
    check_traversal(client)
    check_cors(client)
    check_shared(client)
    check_web(client)
    first_item = check_items(client)
    check_artwork(client, first_item)
    if args.video:
        check_video(client, status_payload)
    if args.share_file:
        check_share_upload(client, args.share_file)

    failures = [r for r in results if r[0] == FAIL]
    warnings = [r for r in results if r[0] == WARN]
    print(f"\n{len(results)} 项检查：{len(results) - len(failures) - len(warnings)} 通过，"
          f"{len(warnings)} 警告，{len(failures)} 失败")
    if failures:
        for _, name, detail in failures:
            print(f"  ✗ {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
