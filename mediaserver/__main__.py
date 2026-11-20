"""``python -m mediaserver`` entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from . import create_app
from .config import Config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mediaserver",
        description="家庭多媒体服务器:音乐、视频、图片、文档 + web 客户端 + HLS 转码",
    )
    parser.add_argument("--root", metavar="DIR", help="音乐目录(等值环境变量 MUSIC_ROOT)")
    parser.add_argument("--data", metavar="DIR", help="可写数据目录(等值 DATA_DIR)")
    parser.add_argument("--host", metavar="ADDR", help="监听地址,默认 0.0.0.0")
    parser.add_argument("--port", type=int, metavar="PORT", help="监听端口,默认 8080")
    parser.add_argument("--dev", action="store_true", help="使用 Flask 开发服务器(仅本机调试)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("mediaserver")

    try:
        config = Config.from_env(root=args.root, data=args.data,
                                 host=args.host, port=args.port)
    except ValueError as exc:
        log.error("配置错误:%s", exc)
        return 2

    app = create_app(config)
    log.info("library=%s data=%s version=%s", config.music_root, config.data_dir,
             config.version)
    log.info("ffmpeg=%s ffprobe=%s hls=%s",
             app.extensions["mediaserver"].library.tools.ffmpeg or "missing",
             app.extensions["mediaserver"].library.tools.ffprobe or "missing",
             config.hls_enabled)
    if not config.auth_enabled and config.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("鉴权关闭:只应在受信局域网内监听;外网访问请设置 "
                    "MEDIA_AUTH_MODE=token 与 MEDIA_TOKEN")

    if args.dev:
        app.run(host=config.host, port=config.port, debug=True, threaded=True)
        return 0

    try:
        from waitress import serve

        log.info("listening on http://%s:%d", config.host, config.port)
        serve(app, host=config.host, port=config.port, threads=8,
              channel_timeout=180, connection_limit=200)
    except ImportError:
        log.warning("waitress 未安装,退回 Flask 服务器(生产环境请安装 waitress)")
        app.run(host=config.host, port=config.port, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
