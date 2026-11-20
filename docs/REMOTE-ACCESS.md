# 外网访问（VPS 反向隧道 + nginx + 令牌）

MediaServer 本身按「受信局域网」设计，**不要**把 8080 直接映射到公网。
需要在外网使用时，用一台有公网 IP 的 VPS 做**反向隧道 + TLS 反代**：

```text
 手机 / 电脑
     │  HTTPS(443)
     ▼
 ┌──────────── VPS ────────────┐
 │ nginx (TLS, 限流, 可选 basic auth)
 │   └─ 127.0.0.1:18080
 │        ▲
 │        │ SSH 反向隧道（仅本机回环）
 └────────┼────────────────────┘
          │
 ┌────────┴──────── NAS ───────┐
 │ autossh → 127.0.0.1:8080    │
 │ MediaServer (token 模式)     │
 └─────────────────────────────┘
```

好处：NAS 不开放任何入站端口；VPS 只暴露 443；TLS 与限流在 VPS 终止；
应用层还有一层令牌。文档里的 `media.example.com` / `vps.example.com` 都是占位。

## 1. 在 NAS 上开启令牌模式

```bash
TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "$TOKEN"   # 存进密码管理器，之后登录网页与 API 都用它

sudo tee /etc/mediaserver/env >/dev/null <<EOF
MUSIC_ROOT=/srv/music
MEDIA_DATA_DIR=/var/lib/mediaserver
MEDIA_HOST=127.0.0.1
MEDIA_PORT=8080
MEDIA_AUTH_MODE=token
MEDIA_TOKEN=$TOKEN
MEDIA_TRUST_PROXY=1
MEDIA_COOKIE_SECURE=1
MEDIA_PUBLIC_URL=https://media.example.com
EOF
sudo systemctl restart mediaserver
curl -fsS http://127.0.0.1:8080/api/v1/status   # status 仍公开，便于健康检查
```

`MEDIA_HOST=127.0.0.1` 让局域网也无法直连——如果你还需要局域网直连，
改成局域网地址，并确认真实路由器上没有 8080 的端口映射。

## 2. 在 VPS 上初始化

```bash
# 以 root 运行，脚本会安装 nginx/certbot/fail2ban、创建 tunnel 用户并申请证书
sudo ./deploy/tunnel/setup-vps.sh media.example.com you@example.com

# 放行 80/443（按你的防火墙工具）
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

脚本会把 `tunnel` 用户限制为**只能做远程端口转发**（`PermitTTY no`、`AllowTcpForwarding remote`、
仅公钥），并生成：

- `/etc/nginx/mediaserver-proxy.conf`：关闭 buffering、透传 `Range`/上传；
- `/etc/nginx/sites-available/mediaserver`：443 + HSTS + 登录限流；
- `/etc/fail2ban/jail.d/mediaserver.conf`。

## 3. 建立隧道

在 NAS 上：

```bash
sudo apt-get install -y autossh
sudo ssh-keygen -t ed25519 -N '' -f /etc/mediaserver/tunnel_key
sudo ssh-keyscan -H vps.example.com | sudo tee /etc/mediaserver/known_hosts

# 把公钥加到 VPS 的 tunnel 用户
sudo ssh-copy-id -i /etc/mediaserver/tunnel_key.pub tunnel@vps.example.com
# 或手动追加到 VPS: /home/tunnel/.ssh/authorized_keys

sudo tee /etc/mediaserver/tunnel.env >/dev/null <<'EOF'
VPS_HOST=vps.example.com
VPS_USER=tunnel
VPS_PORT=22
REMOTE_PORT=18080
LOCAL_PORT=8080
IDENTITY=/etc/mediaserver/tunnel_key
EOF
sudo chmod 600 /etc/mediaserver/tunnel.env /etc/mediaserver/tunnel_key
sudo chown mediaserver:mediaserver /etc/mediaserver/tunnel.env /etc/mediaserver/tunnel_key

sudo cp deploy/tunnel/mediaserver-tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mediaserver-tunnel
sudo systemctl status mediaserver-tunnel --no-pager
```

`-R 127.0.0.1:18080:127.0.0.1:8080` 只在 VPS 回环上监听，公网扫不到 18080。
隧道断了 systemd 会自动重连（`Restart=always` + autossh keepalive）。

## 4. 验证

```bash
# 从任意外网机器
curl -fsS https://media.example.com/api/v1/status
curl -fsS -H "Authorization: Bearer $TOKEN" \
     https://media.example.com/api/v1/search?q=test
curl -sI -H 'Range: bytes=0-1023' \
     https://media.example.com/media/music/example.mp3 | head
```

浏览器打开 `https://media.example.com/login`，输入 `MEDIA_TOKEN`。

## 5. 加固清单

- [ ] `MEDIA_AUTH_MODE=token`，令牌 ≥ 32 字节，只存在密码管理器与 `0600` 的 env 文件；
- [ ] `MEDIA_HOST=127.0.0.1`，路由器/云安全组没有 8080 入站规则；
- [ ] VPS 只开 22/80/443；22 端口限制来源或改端口；
- [ ] nginx 开 HSTS、`server_tokens off`、登录限流；
- [ ] fail2ban 生效：`fail2ban-client status nginx-http-auth`；
- [ ] `MEDIA_COOKIE_SECURE=1`、`MEDIA_TRUST_PROXY=1` 且反代确实设置 `X-Forwarded-*`；
- [ ] 定期 `apt upgrade`、`pipenv install` 更新依赖；
- [ ] 备份 `/var/lib/mediaserver`；
- [ ] 不需要外网时直接 `systemctl stop mediaserver-tunnel` 并关掉 VPS 的 443。

## 6. 备选方案

| 方案 | 说明 |
|---|---|
| frp | `frpc` 在 NAS，`frps` 在 VPS；把 `remote_port` 绑到 `127.0.0.1`，同样用 nginx 反代 |
| Cloudflare Tunnel | 不需要公网 IP；`cloudflared` 出站连接；注意免费版 100MB 上传限制 |
| Tailscale / WireGuard | 只给设备组网，不暴露公网；最省心的私密方案 |
| 端口映射 | ❌ 不建议：8080 直接暴露等于把无鉴权接口交给互联网 |

## 7. 安全边界

- 应用层令牌只能防止未授权访问，**不能**替代 TLS：没有 TLS 时令牌会明文经过网络；
- 上传播放的文件仍由 ffmpeg/mutagen 解析，令牌泄露等于对方可以上传任意媒体，务必使用长随机令牌；
- `/api/v1/status` 在令牌模式下仍公开（健康检查与客户端「测试连接」需要），只暴露计数与能力，不含路径；
- 反向隧道断线时服务不可用，不会自动降级成无鉴权。
