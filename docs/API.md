# API 参考

所有接口默认监听 `http://<host>:8080`。示例里的 `192.0.2.10` 是文档保留地址。
除 `/api/v1/status`、`/healthz`、`/login` 与静态资源外，开启令牌认证后都需要
`Authorization: Bearer <MEDIA_TOKEN>`（网页用签名 Cookie）。

成功响应是 JSON，错误响应是 `{"error": "..."}`，状态码语义：

| 状态码 | 含义 |
|---|---|
| 200 / 201 / 202 / 204 | 成功 / 已创建 / 已接受（后台任务） |
| 400 | 参数错误 |
| 401 | 未认证（令牌模式） |
| 403 | 来源被拒绝（CORS / CSRF） |
| 404 | 资源不存在 |
| 409 | 转码尚未完成 |
| 413 | 超过上传大小限制 |
| 415 | 不支持的媒体类型 |
| 416 | Range 越界 |
| 501 | 功能未启用 |

## 契约接口（NAS-DEPLOY §6）

### `GET /api/v1/status`

```json
{
  "ok": true,
  "gateway": "mediaserver",
  "gatewayVersion": "1.3.0",
  "counts": {"audio": 12, "video": 2, "image": 3, "doc": 4, "total": 21},
  "scan": {"lastScan": 1760000000, "lastScanDuration": 0.42, "scanning": false, "interval": 300},
  "features": {"music": true, "video": true, "hls": true, "share": true,
               "relay": false, "auth": false, "ffmpeg": true, "ffprobe": true},
  "share": {"format": "muYunShared/1", "enabled": true, "maxUploadMB": 300},
  "video": {"profiles": ["720p"], "segmentSeconds": 6}
}
```

`ok:true` 与 `gatewayVersion` 同时提供，供客户端「测试连接」判定。

### `GET /api/v1/search?q=&limit=&offset=&sources=local`

```json
{
  "format": "muYunSearch/1",
  "query": "月光",
  "sources": ["local"],
  "results": [
    {
      "id": "local:music/album/01 - 月光.flac",
      "itemId": "a1b2c3d4e5f60718293a",
      "title": "月光",
      "artist": "演示歌手",
      "album": "Synthetic Sessions",
      "duration": 202.4,
      "playUrl": "/media/music/album/01%20-%20%E6%9C%88%E5%85%89.flac",
      "download": "/api/v1/download?format=raw&item=a1b2c3d4e5f60718293a",
      "cover": "/api/v1/cover/a1b2c3d4e5f60718293a?v=1760000000",
      "lyricsUrl": "/api/v1/lyrics/a1b2c3d4e5f60718293a",
      "source": "local",
      "kind": "audio"
    }
  ],
  "offset": 0,
  "limit": 30,
  "sourceErrors": {}
}
```

- `limit` 上限 100；`offset` 上限 20000；
- `playUrl` 永远是同源相对路径；
- 没有有效 `playUrl` 的条目不会进入 `results`。

### `GET /api/v1/manifest`

`muYunImport/1` 清单，地址是绝对 URL（协议 + Host 由请求或 `MEDIA_PUBLIC_URL` 决定）：

```json
{
  "schema": "muYunImport/1",
  "generatedAt": 1760000000,
  "tracks": [
    {
      "url": "http://192.0.2.10:8080/media/music/album/01%20-%20%E6%9C%88%E5%85%89.flac",
      "title": "月光",
      "artist": "演示歌手",
      "album": "Synthetic Sessions",
      "duration": 202.4,
      "cover": "http://192.0.2.10:8080/api/v1/cover/a1b2c3d4e5f60718293a?v=1760000000",
      "tags": ["flac", "44kHz"]
    }
  ]
}
```

空曲库返回 `tracks: []`，不报错。

### `GET /media/<path>`

返回文件本体，支持：

- `Range: bytes=...` → `206` + `Content-Range` + `Accept-Ranges: bytes`；
- 越界 Range → `416`；
- `If-None-Match` / `If-Modified-Since` → `304`；
- `HEAD` 返回同样的头部；
- 路径穿越（`..`、编码变体、绝对路径）、隐藏文件、未知挂载一律 `404`。

所有媒体 URL 都挂在这一个前缀下：`/media/music/…`、`/media/videos/…`、
`/media/images/…`、`/media/docs/…`、`/media/shared/…`、`/media/uploads/…`。

### `GET /relay/<url|pic|lrc>/<server>/<id>`

可选功能，要求 `MEDIA_RELAY_ENABLED=1`。把上游（Meting 协议）音频/封面/歌词
中转成本机同源地址：

- 每一跳重定向都做私网地址检查（SSRF 防护），最多 6 跳；
- `url` 透传 `Range`，分块转发，不整首读进内存；
- `pic` / `lrc` 落内存缓存（上限 400 条）。

未启用时返回 `404`，不会静默失败。

### `POST /api/v1/share`

**multipart（本地文件）**

| 字段 | 说明 |
|---|---|
| `file` | 文件本体（必填） |
| `kind` | 固定 `file` |
| `title` / `artist` / `from` | 展示信息 |
| `duration` | 秒（可选） |

**application/json（同源 URL）**

```json
{"kind": "url", "url": "/media/music/a.flac", "title": "歌名", "from": "昵称"}
```

`url` 只接受本服务器的 `/media/...` 或 `/relay/url/...`，其他主机一律 `400`。

成功返回 `201`：

```json
{"id": "share-20261120120000-ab12cd", "kind": "file", "title": "歌名",
 "artist": "歌手", "duration": 202.4, "playUrl": "/media/shared/share-20261120120000-ab12cd.flac",
 "from": "昵称", "sharedAt": 1760000000, "size": 123456, "mime": "audio/flac"}
```

### `GET /api/v1/shared`

```json
{"format": "muYunShared/1", "items": [
  {"id": "share-…", "title": "歌名", "artist": "歌手", "duration": 202.4,
   "playUrl": "/media/shared/share-….flac", "cover": "/media/shared/covers/share-….jpg",
   "from": "昵称", "sharedAt": 1760000000}
]}
```

最多保留 500 条，按 `sharedAt` 倒序。

## 扩展接口

### 媒体库

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/library` | 按 `audio/video/image/doc` 分组的全量列表（每类上限 10000） |
| GET | `/api/v1/items?kind=&q=&sort=&limit=&offset=&artist=&album=` | 分页列表；`sort` ∈ `recent/mtime/name/artist/duration/size` |
| GET | `/api/v1/items/<id>` | 单品详情，附带 `related`、`position`、所属播放列表 |
| GET | `/api/v1/cover/<id>` | 封面（JPEG，可缓存一天） |
| GET | `/api/v1/thumb/<id>?w=480` | 缩略图（视频取第 5 秒画面，图片缩放） |
| GET | `/api/v1/lyrics/<id>` | 原始 LRC 文本；`?format=json` 返回解析后的时间轴 |
| GET | `/api/v1/download?item=<id>` | 附件下载（`Content-Disposition`） |
| POST | `/api/v1/rescan` | 触发一次后台扫描，返回 `202` |
| POST | `/api/v1/upload` | multipart `file`，落 `DATA_DIR/uploads/`，返回 `201` |
| GET | `/api/v1/albums` | 专辑聚合（名称、歌手、曲目数、封面） |
| GET | `/api/v1/artists` | 歌手聚合（曲目数、专辑数、封面） |
| GET | `/api/v1/favorites` | 收藏列表 |
| PUT | `/api/v1/favorites/<id>` | 收藏 |
| DELETE | `/api/v1/favorites/<id>` | 取消收藏 |

`item` 对象字段：`id kind rel url rawUrl title artist album duration width height
videoCodec audioCodec bitrate size mtime mime cover lyricsUrl hasCover hasLyrics
genre year`（视频额外有 `thumb`）。

### 播放进度

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/progress` | `{"itemId","position","duration","played"}` |
| GET | `/api/v1/progress/recent?kind=&limit=` | 「接着播放」，返回 item + `position` |

### 播放列表

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/playlists` | 列表（含条目数） |
| POST | `/api/v1/playlists` | `{"name": "..."}` |
| GET | `/api/v1/playlists/<id>` | 条目列表 |
| POST | `/api/v1/playlists/<id>/items` | `{"itemId": "..."}` |
| DELETE | `/api/v1/playlists/<id>/items/<itemId>` | 移除条目 |
| DELETE | `/api/v1/playlists/<id>` | 删除列表 |

### 视频转码（HLS）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/transcode/<itemId>` | `{"profile": "720p"}`（可省略）；`202` + job；已就绪返回 `200` |
| GET | `/api/v1/jobs/<jobId>` | `{status: idle/queued/running/ready/error, progress, playlist, error}` |
| GET | `/api/v1/hls/<jobId>/index.m3u8` | 就绪后返回 VOD 播放列表，未就绪 `409` |
| GET | `/api/v1/hls/<jobId>/seg00000.ts` | MPEG-TS 分片 |

`jobId` 形如 `<20 位 item id>-<高度>p`。转码输出在 `DATA_DIR/cache/hls/`，
按 `MEDIA_HLS_CACHE_MB` 淘汰最久未用的任务目录。

网页播放器在视频不是 H.264/AAC MP4/WebM 时会自动提交转码任务并轮询进度。

## 认证

```bash
# API 客户端
curl -H "Authorization: Bearer $MEDIA_TOKEN" http://127.0.0.1:8080/api/v1/items

# 或
curl -H "X-API-Token: $MEDIA_TOKEN" http://127.0.0.1:8080/api/v1/items
```

网页登录：`GET /login` 表单 → `POST /login`（字段 `token`、`next`），成功后写入
`HttpOnly` 签名 Cookie；`POST /logout` 退出。失败尝试按 IP 限速（5 分钟 8 次）。

扫码登录：`/connect` 为已登录用户生成 `https://<host>/login?ot=<token>`，
有效期 120 秒、服务端内存记录一次性使用；扫码后手机直接进入已登录状态。

## 网页路由

| 路径 | 页面 |
|---|---|
| `/` | 首页（统计、接着播放、最近添加） |
| `/library?kind=&q=&sort=&page=` | 媒体库（`kind=audio` 会跳到 `/music`） |
| `/music?tab=songs\|albums\|artists\|favorites\|recent` | 音乐中心 |
| `/listen/<id>` | 音频播放器（队列、歌词同步） |
| `/watch/<id>` | 视频播放（直链或 HLS） |
| `/view/<id>` | 图片查看 |
| `/read/<id>` | 文档预览（文本 / PDF） |
| `/shares` | 分享列表与上传 |
| `/connect` | 扫码连接（网页地址、manifest、一次性登录码） |
| `/playlists`、`/playlist/<id>` | 播放列表 |
| `/settings` | 状态、能力、安全提示、重新扫描 |
| `/login`、`/logout` | 登录 / 退出 |
| `/healthz` | 健康检查（无需认证） |
