# 部署

三种方式任选：本地 pipenv、Docker Compose、systemd。目录约定保持一致：

| 挂载 | 权限 | 内容 |
|---|---|---|
| `/music` | 只读 | 音乐、同名 `.lrc`、封面 |
| `/library/{videos,images,docs}` | 只读 | 其他媒体 |
| `/data` | 可写 | 索引、缓存、分享、上传、`.secret` |

## 方式一：pipenv + systemd

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin mediaserver
sudo mkdir -p /opt/mediaserver /var/lib/mediaserver /etc/mediaserver
sudo chown -R mediaserver:mediaserver /opt/mediaserver /var/lib/mediaserver

sudo -u mediaserver git clone https://github.com/zhipengge/MediaServer.git /opt/mediaserver
cd /opt/mediaserver
sudo -u mediaserver env PIPENV_VENV_IN_PROJECT=1 pipenv install

sudo install -m 0640 -o root -g mediaserver /dev/null /etc/mediaserver/env
sudo tee /etc/mediaserver/env >/dev/null <<'EOF'
MUSIC_ROOT=/srv/music
MEDIA_VIDEO_ROOT=/srv/media/videos
MEDIA_IMAGE_ROOT=/srv/media/images
MEDIA_DOC_ROOT=/srv/media/docs
MEDIA_DATA_DIR=/var/lib/mediaserver
MEDIA_HOST=0.0.0.0
MEDIA_PORT=8080
MEDIA_AUTH_MODE=off
EOF

sudo cp deploy/systemd/mediaserver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mediaserver
curl -fsS http://127.0.0.1:8080/api/v1/status
```

注意：

- `PIPENV_VENV_IN_PROJECT=1` 让 `.venv` 落在 `/opt/mediaserver/.venv`；
- `EnvironmentFile` 里的值不要带引号（systemd 会原样当值）；
- 若服务用户对 `/srv/music` 没有读权限，`scan` 会跳过这些文件，日志里能看到 warning。

## 方式二：Docker Compose

```bash
cd deploy/docker
cp .env.example .env
vi .env            # MUSIC_HOST_DIR / MEDIA_HOST_DIR / DATA_HOST_DIR
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100
```

要点：

- 镜像内自带 `ffmpeg` / `ffprobe`，视频缩略图与 HLS 直接可用；
- 根文件系统 `read_only: true`，唯一可写挂载是 `/data`；
- `PUID` / `PGID` 按 NAS 共享目录属主填写；
- healthcheck 是 `curl /healthz`，失败 3 次后容器标记 unhealthy；
- 端口只映射到宿主机；**不要**直接做公网端口转发，见 [REMOTE-ACCESS](REMOTE-ACCESS.md)。

## 方式三：群晖 / 威联通

1. Container Manager / Docker 里新建项目，粘贴 `deploy/docker/compose.yaml`；
2. 把音乐共享目录映射到 `/music`，**只读**；
3. 其他媒体目录映射到 `/library`，只读；
4. 单独建一个可写目录映射到 `/data`；
5. 防火墙只放行家庭网段 → 8080（或干脆只绑内网地址）。

## 权限

- 服务进程只需要：读曲库、读写 `DATA_DIR`；
- 分享与上传只写 `DATA_DIR`，不会修改曲库；
- `DATA_DIR` 必须与曲库互不包含（启动时校验，违反直接退出）；
- `DATA_DIR/.secret` 是会话签名密钥，权限 `0600`，**不要**提交到 git。

## 反向代理

网页和 App 都按相对路径工作；如果放在 nginx 后面：

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host $host;
proxy_buffering off;          # 大文件与 Range
proxy_request_buffering off;  # 上传
client_max_body_size 1g;
```

同时设置：

```bash
MEDIA_PUBLIC_URL=https://media.example.com
MEDIA_TRUST_PROXY=1
MEDIA_COOKIE_SECURE=1
MEDIA_AUTH_MODE=token
MEDIA_TOKEN=<至少 32 字节随机串>
```

`MEDIA_PUBLIC_URL` 决定 manifest 里的绝对地址；不设时按请求 Host 生成。

## 备份与升级

```bash
# 备份（索引、分享、进度、播放列表）
tar czf mediaserver-data-$(date +%F).tgz -C /var/lib mediaserver

# 升级
cd /opt/mediaserver
sudo -u mediaserver git pull
sudo -u mediaserver env PIPENV_VENV_IN_PROJECT=1 pipenv install
sudo systemctl restart mediaserver
curl -fsS http://127.0.0.1:8080/api/v1/status | jq .
```

- 数据目录向后兼容；SQLite schema 只做新增（`CREATE TABLE IF NOT EXISTS`）；
- 回滚：`git checkout <tag>` + `pipenv install` + 重启；
- 删掉 `DATA/cache/hls` 只是丢失转码缓存，下次按需重建。

## 排错

| 现象 | 排查 |
|---|---|
| `/api/v1/status` 不是 JSON | 端口被别的服务占用；看 `journalctl -u mediaserver` |
| 搜索 0 结果 | 目录权限 / 挂载路径错；设置页点「重新扫描」后看 `counts` |
| 播放不能拖动 | 反代开了 `proxy_buffering`，或没透传 `Range` |
| 视频一直“转码中” | 服务器没有 `ffmpeg`，或 `MEDIA_HLS_ENABLED=0`；看日志里的 ffmpeg stderr |
| 上传 413 | 同时调大 `MEDIA_UPLOAD_MAX_MB` 与 nginx `client_max_body_size` |
| 时间不对 | 容器/主机 `TZ` 未设置 |
