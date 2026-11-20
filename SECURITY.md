# 安全说明

## 支持范围

| 版本 | 状态 |
|---|---|
| 1.3.x | 接收安全报告 |
| < 1.3 | 不维护 |

## 报告问题

请使用 GitHub 的 **Private vulnerability reporting**（仓库 Security 页面 →
Report a vulnerability）提交，不要在公开 issue 里放可复现的攻击细节。
本项目是个人向的家庭媒体服务器，响应时间尽力而为，不提供 SLA。

## 威胁模型

**资产**

- 媒体文件本身（隐私照片、家庭视频、音乐）；
- `MEDIA_TOKEN` 与 `DATA_DIR/.secret`；
- 索引里的文件名、路径、标签；
- 上传/分享接口可写入的磁盘空间。

**信任边界**

- 默认假设：服务器只被家庭局域网内的受信设备访问，局域网内没有恶意主机；
- 一旦通过 VPS / 端口映射暴露到互联网，此假设**不再成立**，必须启用令牌与 TLS；
- 第三方上游（Meting relay）不可信，返回内容只作为字节流转发，不执行、不解析为 HTML。

**不在范围内**

- 多用户 / 账号体系 / 每用户权限；
- 病毒扫描、DRM、版权内容审核；
- 物理访问、NAS 自身漏洞、操作系统补丁。

## 默认安全姿态

| 项 | 默认 | 说明 |
|---|---|---|
| 鉴权 | 关闭 | 仅局域网；与 NAS-DEPLOY §9 一致 |
| 监听 | `0.0.0.0:8080` | 部署文档要求防火墙只放行家庭网段 |
| CORS | 关闭 | 不发送 `Access-Control-Allow-Origin` |
| 分享/上传大小 | 300 MB / 1024 MB | 可用环境变量收紧 |
| 目录穿越 | 拒绝 | `..`、编码变体、绝对路径、隐藏文件一律 404 |
| 符号链接 | 只接受落在挂载点内的文件 | 扫描与请求都做 `resolve()` 包含性检查 |
| curl 请求体 | Flask `MAX_CONTENT_LENGTH` | 超过直接 413 |
| 密钥 | 自动生成 `DATA/.secret`（0600） | 也可用 `MEDIA_SECRET_KEY` 注入 |

## 已实施的控制

### 认证与会话

- `MEDIA_AUTH_MODE=token` 时，除 `/api/v1/status`、`/healthz`、`/login`、静态资源外全部要求认证；
- API 支持 `Authorization: Bearer` 与 `X-API-Token`，比较使用 `hmac.compare_digest`（定时安全）；
- 网页登录写入 `HttpOnly`、`SameSite=Lax` 的签名 Cookie；HTTPS 部署可开 `MEDIA_COOKIE_SECURE=1`；
- 登录失败按 IP 限速：5 分钟 8 次后拒绝，成功登录清零；nginx 侧还有 `limit_req` 与 fail2ban；
- 扫码登录（`/connect`）使用 itsdangerous 签名、120 秒有效、服务端记录一次性使用；该链接等价于完整权限，页面已提示不要截图外传；
- Cookie 认证的写操作校验 `Origin` / `Referer`，跨站请求返回 403（CSRF 防护）。

### 文件与路径

- 所有媒体 URL 都经过 `safe_rel()` + `resolve_rel()`：拒绝 `..`、反斜杠、控制字符、隐藏文件，
  解析后必须仍在挂载根内；
- `/media/` 只提供已存在的普通文件，不列目录；
- 上传/分享的文件名经过 `sanitize_filename()`，丢弃路径分隔符与控制字符，只允许已知媒体后缀；
- 分享的 URL 分享只接受本服务器 `/media/`、`/relay/url/` 路径，其他主机 400。

### 注入与跨站

- SQL 全部参数化（sqlite3 placeholders），没有字符串拼接用户输入；
- Jinja2 自动转义；嵌入页面的 JSON 使用 `|tojson`；前端 DOM 更新使用 `textContent`；
- CSP：`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self'`，
  页面没有内联脚本；`X-Frame-Options: SAMEORIGIN`、`nosniff`、`Referrer-Policy: no-referrer`；
- `Host` 头只用于生成 manifest 的绝对地址，先经过字符集校验，校验失败回退到配置值。

### SSRF

- `MEDIA_RELAY_ENABLED=0` 默认关闭；
- 打开后，relay 手动跟随最多 6 跳重定向，每一跳都解析主机名，拒绝私网/环回/链路本地/保留地址；
- 自建上游若在 `127.0.0.1`，必须显式 `MEDIA_RELAY_ALLOW_PRIVATE=1`（明确接受风险）。

### 资源消耗

- 分享 500 条上限、封面/缩略图/HLS 缓存都有容量上限与 LRU 淘汰；
- 转码线程池上限 `MEDIA_HLS_CONCURRENCY`（默认 2），同一视频不会重复起任务；
- 搜索 `limit ≤ 100`、`offset ≤ 20000`；SSE/长连接不存在，全部请求有超时；
- 所有外呼（relay）设置连接/读取超时。

### 子进程

- `ffmpeg` / `ffprobe` 只通过参数数组调用（`shell=False`），路径来自挂载校验后的真实文件；
- 转码输出目录固定、分片名由 ffmpeg 生成，请求侧只接受 `seg\d{5}\.ts`；
- 任务失败会清理 staging 目录，不留下半成品。

## 安全审查记录

**2026-11-20 · 1.3.0 首次发布前审查**

| 检查 | 结果 |
|---|---|
| 路径穿越（原始 / URL 编码 / 多段 `..` / 隐藏文件） | 通过，测试覆盖 |
| 媒体 Range / 416 / 304 行为 | 通过 |
| 令牌错误、缺失、过短；常量时间比较 | 通过 |
| Cookie CSRF（跨站 Origin / Referer） | 通过 |
| CORS 默认关闭；仅精确来源放行 | 通过 |
| 分享同源限制、大小限制、空文件 | 通过 |
| 上传后缀白名单、重名处理 | 通过 |
| relay 私网地址拒绝 | 代码审查通过 |
| SQL 注入面（全部参数化） | 代码审查通过 |
| XSS（模板自动转义、无内联脚本、CSP） | 代码审查通过 |
| 依赖漏洞 | `pip list --outdated` 人工检查；锁文件固定版本 |
| 个人信息（IP / 邮箱 / 主机名 / 密钥） | `scripts/security_scan.py` 通过 |
| 仓库内容（`.env`、`data/`、日志未跟踪） | `git ls-files` 检查通过 |

发现的低风险项与处理：

1. `status` 在令牌模式下仍公开 —— 仅暴露计数与能力位，不含路径/文件名，保留用于健康检查与客户端测试连接；
2. 分享列表是全局的，没有「谁分享给谁」的权限模型 —— 明确写进限制，属于单用户家庭场景；
3. 网页设置页展示数据目录/曲库绝对路径 —— 仅在登录后可见；无鉴权模式下局域网可见，属预期。

## 已知限制

- 没有多用户与细粒度权限，令牌即全部权限；
- 上传的媒体由 ffmpeg/mutagen 解析，解析器漏洞不在本项目控制范围内，建议只给受信用户令牌；
- 分享文件不会自动删除，500 条只是数据库索引上限，磁盘需自行清理；
- 可选的 relay 会把第三方音频经本服务器转发，上游可用性、版权与隐私由使用者负责；
- 反向代理/隧道需要自行维护（证书、SSH 密钥、VPS 补丁）。

## 个人信息与发布

本仓库不包含真实内网地址、公网 IP、邮箱、主机名、家庭路径或密钥：

- 文档示例统一使用 `192.0.2.0/24`、`198.51.100.0/24`、`203.0.113.0/24` 与 `example.com`；
- 运行时数据、`.env`、日志、媒体文件全部在 `.gitignore` 中；
- `scripts/security_scan.py` 在本地与 CI 中执行，命中即失败；
- 提交信息不包含内部主机名或个人信息。

## 部署检查清单

- [ ] 公网部署：`MEDIA_AUTH_MODE=token` + ≥32 字节随机令牌 + HTTPS；
- [ ] `MEDIA_COOKIE_SECURE=1`、`MEDIA_TRUST_PROXY=1`（仅当反代可信）；
- [ ] 没有把 8080 直接映射到公网；
- [ ] 防火墙只放行必要来源；
- [ ] `DATA_DIR` 权限 0750，`.secret` 0600；
- [ ] 定期更新依赖与系统补丁；
- [ ] 备份 `DATA_DIR`，并确认备份本身不被公开。
