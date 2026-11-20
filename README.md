# MediaServer

家庭多媒体服务器：把 NAS 上的**音乐、视频、图片、文档**索引成一个统一的 HTTP 服务，
自带网页客户端，并完整兼容牧云音乐的
[NAS-DEPLOY 契约](https://github.com/zhipengge/apps/blob/main/muyunmusic/NAS-DEPLOY.md)
（`/api/v1/status`、`/search`、`/manifest`、`/media`、`/relay`、`/share`、`/shared`）。

- **后端**：Flask + SQLite（索引 / 播放进度 / 播放列表 / 分享）
- **前端**：Jinja2 + 原生 ES 模块，无需 Node 构建，断网也能用
- **视频**：浏览器可直接播放的格式走 Range 直链；其余按需转成 HLS（ffmpeg，可选）
- **部署**：本地 pipenv、Docker Compose、systemd 三种方式
- **外网**：通过 VPS 反向隧道 + TLS + 令牌认证访问，见 [docs/REMOTE-ACCESS.md](docs/REMOTE-ACCESS.md)

> 默认按「受信局域网」运行，接口不鉴权（与 NAS-DEPLOY 一致）。**任何暴露到公网的部署都必须
> 开启 `MEDIA_AUTH_MODE=token`**，并在反向代理层再加 TLS。见 [SECURITY.md](SECURITY.md)。

## 功能

| 能力 | 说明 |
|---|---|
| 契约接口 | `status` / `search` / `manifest` / `media`（Range、ETag、304）/ `shared` / `share` |
| 统一索引 | 音频、视频、图片、文档；增量扫描（mtime + size），后台定时，手动触发 |
| 元数据 | mutagen 读取 ID3 / Vorbis / MP4 标签与内嵌封面、歌词；图片尺寸；视频时长与分辨率 |
| 网页客户端 | 首页、媒体库、音乐播放器（LRC 同步高亮）、视频播放、图片查看、文档预览、分享、播放列表、设置 |
| 视频 | MP4/WebM 直链拖动；其他编码按需转 HLS（`ffmpeg`），任务进度可查，缓存可淘汰 |
| 分享 | `POST /api/v1/share`（multipart 文件 / 同源 URL）+ `GET /api/v1/shared` |
| 播放进度 | 断点续播，「接着播放」区块 |
| 安全 | 可选令牌认证 + 签名 Cookie、登录限速、路径穿越防护、CSP、默认关闭 CORS、relay SSRF 防护 |
| 可选外接音源 | Meting 协议中转（默认关闭，仅个人 NAS 自用） |

## 快速开始

需要 Python 3.11+（推荐 3.12）与 pipenv：

```bash
git clone https://github.com/zhipengge/MediaServer.git
cd MediaServer
make setup                 # PIPENV_VENV_IN_PROJECT=1 pipenv install --dev
make seed                  # 生成合成演示媒体（可选）
make run                   # http://127.0.0.1:8080
```

`make setup` 把虚拟环境固定建在项目内的 `.venv/`，不会污染系统 Python。
也可以手工执行：

```bash
export PIPENV_VENV_IN_PROJECT=1
pipenv install --dev
pipenv run python -m mediaserver --root ./media/music --data ./data --port 8080
```

把媒体放进目录即可：

```text
media/                      # MEDIA_LIBRARY_ROOT（或分别配置）
├── music/专辑/01 - 歌名.flac
│   ├── 01 - 歌名.lrc       # 同名歌词（可选）
│   └── cover.jpg           # 同目录封面（可选）
├── videos/电影.mp4
├── images/照片.jpg
└── docs/说明.md
data/                       # 可写：索引、缓存、分享、上传
```

首次启动会后台扫描；也可以在网页「设置 → 重新扫描」。

## 目录约定

| 挂载 | 环境变量 | 权限 | 用途 |
|---|---|---|---|
| 音乐库 | `MUSIC_ROOT`（别名 `MEDIA_MUSIC_ROOT`） | **只读** | 音乐、同名 `.lrc`、封面 |
| 其他媒体 | `MEDIA_VIDEO_ROOT` / `MEDIA_IMAGE_ROOT` / `MEDIA_DOC_ROOT` | 只读 | 视频 / 图片 / 文档 |
| 数据 | `DATA_DIR`（别名 `MEDIA_DATA_DIR`） | 可写 | 索引、缓存、分享、上传 |

启动时校验 `MUSIC_ROOT` 与 `DATA_DIR` 互不包含，避免把缓存/分享当成曲库文件暴露出去。
网页上传与分享**只写 `DATA_DIR`**，永远不改曲库。

## 配置

完整清单见 [.env.example](.env.example)。常用项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MEDIA_HOST` / `MEDIA_PORT` | `0.0.0.0` / `8080` | 监听地址与端口 |
| `MEDIA_AUTH_MODE` | `off` | `off`（局域网）或 `token`（外网必须） |
| `MEDIA_TOKEN` | 空 | 令牌认证口令，至少 16 字符 |
| `MEDIA_PUBLIC_URL` | 空 | 反向代理后的对外地址，用于生成清单绝对 URL |
| `MEDIA_CORS_ORIGIN` | 空 | 精确放行的跨域来源；空 = 不发 CORS 头 |
| `MEDIA_SHARE_MAX_MB` | `300` | 分享上传上限 |
| `MEDIA_UPLOAD_MAX_MB` | `1024` | 网页上传上限 |
| `MEDIA_SCAN_INTERVAL` | `300` | 自动扫描间隔（秒），`0` 关闭 |
| `MEDIA_HLS_ENABLED` | `1` | 是否允许视频转码 |
| `MEDIA_HLS_HEIGHT` | `720` | HLS 目标高度 |
| `MEDIA_FFMPEG` / `MEDIA_FFPROBE` | 自动 | 指定二进制；缺省用 PATH 或 `imageio-ffmpeg` |
| `MEDIA_RELAY_ENABLED` | `0` | 可选 Meting 中转 |
| `MEDIA_TRUST_PROXY` | `0` | 仅当可信反代设置 `X-Forwarded-*` 时打开 |

## 接口

契约接口与 NAS-DEPLOY §6 一一对应，扩展接口见 [docs/API.md](docs/API.md)：

```bash
curl -s http://192.0.2.10:8080/api/v1/status
curl -sG http://192.0.2.10:8080/api/v1/search --data-urlencode 'q=歌名'
curl -s http://192.0.2.10:8080/api/v1/manifest
curl -sI -H 'Range: bytes=0-1023' http://192.0.2.10:8080/media/music/专辑/01.flac
curl -s http://192.0.2.10:8080/api/v1/shared
```

网页客户端与 API 同源，因此 `cover`、`lyricsUrl`、`playUrl` 一律是服务器自己的相对路径，
不会把第三方平台域名交给客户端。

## Docker

```bash
cd deploy/docker
cp .env.example .env        # 改成自己的路径（.env 已被 gitignore）
docker compose up -d --build
docker compose ps
```

镜像带 `ffmpeg`、`curl`、`gosu`；容器根文件系统只读，写只发生在 `/data`；
曲库按 `:ro` 挂载；healthcheck 就是契约里的 `/api/v1/status`。详见 [docs/DEPLOY.md](docs/DEPLOY.md)。

## 外网访问

推荐拓扑：

```text
浏览器/App ──HTTPS──> VPS(nginx + Let's Encrypt)
                        │  反向隧道（autossh / frp）
                        ▼
                     NAS:127.0.0.1:8080（MediaServer, token 模式）
```

服务本身只监听局域网；VPS 只暴露 443。隧道、证书、systemd 单元和
fail2ban 规则都在 [docs/REMOTE-ACCESS.md](docs/REMOTE-ACCESS.md) 与 `deploy/tunnel/` 里。

## 验收

```bash
pipenv run pytest -q                                  # 单元 / 集成测试
pipenv run python scripts/selfcheck.py --base http://127.0.0.1:8080
pipenv run python scripts/selfcheck.py --base http://127.0.0.1:8080 --video --share-file demo.mp3
pipenv run python scripts/security_scan.py .          # 发布前个人信息扫描
```

`selfcheck.py` 覆盖契约字段、同源、Range/206、路径穿越、默认 CORS、分享、网页与（可选）HLS 全链路。

## 与 NAS-DEPLOY 的关系

- 音乐客户端（牧云音乐）仍按原契约工作：填 `http://<NAS-IP>:8080` → 测试连接 → 搜索 / 播放 / 拖动 / 分享；
- `manifest` 是绝对 URL，按请求 Host 或 `MEDIA_PUBLIC_URL` 生成，模拟器与真机共用同一份清单；
- 所有媒体 URL 同源；不聚合第三方平台曲库（NAS-DEPLOY §0 明确排除）；
- 分享文件落 `DATA_DIR/shared/`，与曲库隔离，不进 manifest / 搜索。

## 许可

MIT，见 [LICENSE](LICENSE)。演示媒体全部由脚本本地合成，不附带第三方素材。
