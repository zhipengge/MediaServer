# 架构

## 模块

```text
mediaserver/
├── __init__.py      create_app：服务装配、鉴权钩子、CSP/CORS、错误页
├── __main__.py      CLI（--root/--data/--host/--port/--dev），waitress 启动
├── config.py        环境变量 → 不可变 Config；目录互斥校验
├── db.py            SQLite schema 与查询（items/progress/playlists/shares）
├── library.py       扫描、增量索引、封面/缩略图、歌词读取、缓存淘汰
├── metadata.py      mutagen / ffprobe / MP4 box 解析 / Pillow
├── ffmpeg.py        ffmpeg/ffprobe 定位（PATH 或 imageio-ffmpeg）
├── media.py         Range/ETag 文件响应
├── share.py         muYunShared/1 分享
├── video.py         HLS 任务、进度、分片、缓存淘汰
├── auth.py          令牌校验、签名 Cookie、登录限速
├── api/             JSON API（契约 + 扩展）
├── web.py           网页路由（Jinja2）
├── templates/       页面
└── static/          CSS / 原生 JS / hls.js
```

## 音乐播放（player-core）

`static/js/player-core.js` 拥有唯一的 `<audio>` 与队列：

- 队列与播放模式存 `localStorage`，跨页面刷新恢复（音频本身会随页面卸载停止，恢复后为暂停态）；
- 模式：顺序 / 列表循环 / 单曲 / 随机；进度每 10 秒写回播放进度；
- Media Session 提供锁屏、耳机键控制；快捷键：空格、←/→ 5 秒、Shift+←/→ 切歌；
- 音乐页（`/music`）由服务端渲染，播放/收藏由 `music.js` 调用 `MSPlayer`；
- `/listen/<id>` 保留独立的大播放页，使用自己的队列实现，因此不渲染全局播放条。

## 请求流

```text
浏览器 / App
   │
   ├─ before_request ── 令牌或 Cookie 校验（静态资源、status、login 例外）
   │                    OPTIONS 预检（仅在配置了 CORS 来源时）
   ├─ 路由 ─────────── /api/v1/* → JSON；/media/* → 文件；其余 → HTML
   ├─ after_request ── 安全头（CSP/nosniff/frame）、精确来源 CORS、HSTS
   └─ 错误处理 ─────── /api/* 返回 JSON，网页返回 error.html
```

## 索引

- 扫描挂载点：`music / videos / images / docs / uploads`（`shared` 不参与索引）；
- 每个文件按 `rel = <mount>/<相对路径>` 计算稳定 id：`sha1(rel)[:20]`；
- 增量：`size` 与 `mtime` 都没变就跳过元数据探测；
- 探测失败只计数并继续，不中断整轮扫描；
- 删除文件在扫描末尾按差集清理，同时清理其进度与播放列表条目；
- 后台线程：启动时扫一次，之后按 `MEDIA_SCAN_INTERVAL` 周期执行；手动扫描用同一把锁，
  并发的第二次请求直接返回 `running`；
- SQLite 使用 WAL + 每线程连接 + `busy_timeout`，扫描线程与请求线程互不阻塞。

扫描结果同时写 `scan_state.last_scan`，网页设置页与 `/api/v1/status` 读取它。

## 元数据

| 类型 | 主路径 | 兜底 |
|---|---|---|
| 音频 | mutagen：ID3 / Vorbis / MP4 标签、时长、码率、内嵌封面（APIC/FLAC picture/covr）、歌词（USLT/TXXX/`lyrics`） | ffmpeg 解析时长 |
| 视频 | ffprobe JSON（时长、宽高、编码） | `ffmpeg -i` 文本解析 → 纯 Python ISO-BMFF（mvhd/tkhd）解析 |
| 图片 | Pillow | 无 |

内嵌封面在第一次请求 `/api/v1/cover/<id>` 时才抽取到 `cache/covers/<id>.jpg`，
避免扫描阶段为整库解码图片。视频缩略图同理，在 `/api/v1/thumb/<id>` 时用
`ffmpeg -ss` 取第 5 秒（或时长 10%）的一帧。两类缓存都按容量上限淘汰。

## 媒体响应

`flask.send_file(..., conditional=True)` 负责 Range / 206 / 416 / If-None-Match / 304，
`send_media()` 只补充 `Accept-Ranges` 与安全头。文件流由 WSGI 的 file wrapper 分块写出，
不会整文件读入内存。

## 视频转码

```text
POST /transcode/<itemId>
   ├─ 已有 done 标记 → 200 ready
   ├─ 正在跑 → 返回同一 job（不重复起进程）
   └─ 线程池提交：ffmpeg → staging 目录
          ├─ -progress pipe:1 解析 out_time_ms，按源时长折算百分比
          ├─ 成功：写 done 标记后原子 rename 到正式目录
          └─ 失败：清理 staging，job.error 记录 stderr 末尾
```

输出是单码率 VOD HLS（默认 720p，高度不超过源高度，偶数对齐），
`index.m3u8` 里只有相对分片名，因此浏览器会请求
`/api/v1/hls/<jobId>/seg00000.ts`。转码缓存按 `MEDIA_HLS_CACHE_MB` 淘汰最旧目录，
正在运行的任务不淘汰。

直链优先：`video.direct_playable()` 判断扩展名与编码，H.264/AAC 的 MP4/WebM
根本不走 ffmpeg，浏览器的 Range 拖动体验最好。

## 数据模型

```sql
items(id, rel UNIQUE, kind, size, mtime, title, artist, album, albumartist,
      duration, width, height, video_codec, audio_codec, bitrate, sample_rate,
      has_cover, has_lyrics, lyrics_rel, cover_rel, meta JSON, added_at, indexed_at)

progress(item_id PK, position, duration, updated_at, plays)
playlists(id, name, created_at)
playlist_items(playlist_id, item_id, pos, added_at)
shares(id, kind, title, artist, duration, play_url, cover, from_name,
       shared_at, size, mime)
scan_state(k, v)
```

`meta` 保存 genre/year 与内嵌歌词文本（有大小截断），避免每次读歌词都重开音频文件。

## 目录与缓存

| 路径 | 内容 | 淘汰 |
|---|---|---|
| `DATA/index.sqlite3` | 索引、进度、播放列表、分享 | 不淘汰 |
| `DATA/cache/covers/` | 抽取的封面 JPEG | 单文件缓存，按需重建 |
| `DATA/cache/thumbs/` | 图片/视频缩略图 | `MEDIA_THUMB_CACHE_MB` |
| `DATA/cache/hls/` | 转码输出 | `MEDIA_HLS_CACHE_MB` |
| `DATA/shared/` | 分享文件 | 数据库保留最近 500 条，文件由运维清理 |
| `DATA/uploads/` | 网页上传 | 不淘汰 |
| `DATA/.secret` | Flask 会话签名密钥（0600） | — |

## 为什么是 Flask 而不是异步框架

目标场景是家庭 NAS：并发个位数、瓶颈在磁盘和 ffmpeg，而不是 IO 多路复用。
Flask + waitress（8 线程）足够，且 SQLite、mutagen、ffmpeg 子进程都是同步模型，
不用引入事件循环与线程池混用的复杂度。大文件由 WSGI file wrapper 流式发送，
视频转码在独立线程池，不占用请求线程。

## 扩展点

- **新搜索源**：在 `config.search_sources` 允许的集合里加名字，并在 `api.search`
  增加分支；返回结构沿用 `muYunSearch/1`，地址必须同源。
- **新 HLS 档位**：`VideoService.profile_name()` 与 `start(profile=...)` 已参数化，
  多档位只需在建任务时按分辨率拆分并生成 master playlist。
- **新存储后端**：`LibraryManager` 只依赖 `Database` 的接口与 `config.mounts`，
  替换为对象存储时需要改扫描与 `resolve()` 两处。
