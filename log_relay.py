# -*- coding: utf-8 -*-
"""日志转发 (log relay): 把本机 router 日志通过 HTTP 暴露给远程监控窗口。

背景: llama-server 没有任何日志类 HTTP 接口 (只有 --log-file), 所以远程想看到
和本地一样的日志面板, 只能读到远程机器上的那几个文件。本模块提供一个极小的
只读服务, 让远程的「实时监控」窗口 tail 到和本地一模一样的内容。

接口 (全部只读):
    GET /health              -> {"status":"ok","role":"log-relay","port":N,"dir":D}
    GET /logs/<文件名>?since=<字节偏移>
                            -> {"name","size","offset","reset","data"}
                               data 是从 (对齐后的) since 位置到文件末尾的文本;
                               文件被轮转 (size < since) 时 reset=True 并从头给。
    其它路径                 -> 404

偏移按**字节**记, 与本地 tail 的 os.SEEK_CUR 语义一致; 客户端自己存 offset,
服务端无状态。因为日志是行文本, 截断时会跳过半行, 保证不会在多字节字符中间切开。

用法:
    py log_relay.py                 # 用配置里的 log_dir / port
    py log_relay.py --port 8099 --dir D:\\path\\logs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

ROLE = "log-relay"
DEFAULT_PORT = 8099
# 单次最多回多少字节, 避免首次拉取把 10MB 轮转日志全推过去
MAX_CHUNK = 1 * 1024 * 1024
# 只允许读日志目录下的普通文件, 文件名不能带路径分隔符
_SAFE_NAME = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _json(code: int, obj: Dict[str, Any]) -> Tuple[int, bytes]:
    return code, json.dumps(obj, ensure_ascii=False).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    """每个请求都独立处理; relay 通过 self.server.relay 拿到目录。"""

    server_version = "LogRelay/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:   # 不往 stderr 刷屏
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802 (http.server 命名约定)
        relay: "LogRelay" = self.server.relay  # type: ignore[attr-defined]
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"

        if path == "/health":
            self._send(*_json(200, {
                "status": "ok",
                "role": ROLE,
                "port": relay.port,
                "dir": relay.log_dir,
            }))
            return

        if path.startswith("/logs/"):
            name = path[len("/logs/"):]
            if not name or not set(name) <= _SAFE_NAME:
                self._send(*_json(404, {"error": "bad file name"}))
                return
            try:
                since = int((parse_qs(url.query).get("since") or ["0"])[0])
            except ValueError:
                self._send(*_json(400, {"error": "since must be an integer"}))
                return
            code, obj = _read_chunk(relay.log_dir, name, since)
            self._send(*_json(code, obj))
            return

        self._send(*_json(404, {"error": "not found", "path": path}))


def _read_chunk(log_dir: str, name: str, since: int) -> Tuple[int, Dict[str, Any]]:
    path = os.path.join(log_dir, name)
    if not os.path.isfile(path):
        return 404, {"error": "no such log", "name": name}
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return 500, {"error": f"{type(e).__name__}: {e}", "name": name}

    reset = False
    start = max(0, since)
    if start > size:
        # 文件被轮转了 (router.out.log -> router.out-{ts}.log), 客户端要从头来
        start = 0
        reset = True

    try:
        with open(path, "rb") as f:
            if start > 0:
                # 只有当 since 不在行首时才跳过当前这半行 (保证不会在多字节字符
                # 中间切开); 已经在行首的话 readline() 会把整行吃掉, 增量就空了。
                f.seek(start - 1)
                at_boundary = f.read(1) == b"\n"
                if not at_boundary:
                    f.readline()
            start = f.tell()
            data = f.read(MAX_CHUNK)
            end = f.tell()
            if end < size:
                # 还有更多 -> 本次只给到 MAX_CHUNK, 且必须落在换行符之后
                nl = data.rfind(b"\n")
                if nl >= 0:
                    data, end = data[:nl + 1], start + nl + 1
    except OSError as e:
        return 500, {"error": f"{type(e).__name__}: {e}", "name": name}

    return 200, {
        "name": name,
        "size": size,
        "offset": end,
        "start": start,
        "reset": reset,
        "data": data.decode("utf-8", errors="replace"),
        "has_more": end < size,
    }


class LogRelay:
    """只读日志转发服务。start() 后后台线程常驻, stop() 干净退出。"""

    def __init__(self, log_dir: str, host: str = "0.0.0.0",
                 port: int = DEFAULT_PORT) -> None:
        self.log_dir = log_dir
        self.host = host
        self.port = int(port)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.error: str = ""

    @property
    def running(self) -> bool:
        return self._httpd is not None and self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return True
        try:
            httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        except OSError as e:
            self.error = f"{type(e).__name__}: {e}"
            return False
        httpd.relay = self                          # type: ignore[attr-defined]
        httpd.daemon_threads = True
        # 端口写 0 时由系统分配, 这里记下真实端口
        self.port = httpd.server_address[1]
        self._httpd = httpd
        self.error = ""
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        kwargs={"poll_interval": 0.3},
                                        name="log-relay", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        thread, self._thread = self._thread, None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        if thread is not None:
            thread.join(timeout=3)


def main(argv=None) -> int:
    import config as _config          # 延迟导入, 让模块可独立测试
    try:
        cfg_relay = _config.load().get("log_relay") or {}
    except Exception:
        cfg_relay = {}
    parser = argparse.ArgumentParser(description="router 日志只读转发服务")
    parser.add_argument("--dir", default=os.path.join(_config.app_dir(), "logs"),
                        help="日志目录 (默认: <app_dir>/logs)")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int,
                        default=int(cfg_relay.get("port", DEFAULT_PORT)),
                        help=f"监听端口 (默认 {DEFAULT_PORT})")
    args = parser.parse_args(argv)

    relay = LogRelay(args.dir, host=args.host, port=args.port)
    if not relay.start():
        print(f"启动失败: {relay.error}", file=sys.stderr)
        return 1
    print(f"log relay 听 {relay.host}:{relay.port}  dir={relay.log_dir}  "
          f"(Ctrl+C 退出)")
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        pass
    finally:
        relay.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
