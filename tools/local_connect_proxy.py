#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地 CONNECT 转发器（应急用）
================================
问题：本机代理对 github.com:443 返回 502，但 github.com 的 IP 直连可达。
      代理软件不支持按域名改解析目标，git 也没有 --resolve 选项。

做法：在本机起一个最小 HTTP CONNECT 代理，遇到 github.com 时把 TCP 连接直接
      打到指定 IP，其余域名按原样转发。git 走这个代理就能绕开坏掉的链路。

注意：这是应急手段，不是长期方案。链路恢复后应停掉，改用正常代理。
      仅监听 127.0.0.1，不对局域网开放。

**实测结果（2026-11-10）**：本机未跑通。`curl --resolve` 对该 IP 能拿到 200，
但 TCP 随即被重置，转发器报 TimeoutError——链路层本身不通，脚本绕不过去。
保留此脚本是因为"代理只封主域、不封 API"这种情况确实存在，
下次遇到同类问题可以直接用，不必重写。

用法：
  python tools/local_connect_proxy.py --ip 20.205.243.166 --port 18080
  git -c http.proxy=http://127.0.0.1:18080 push origin main
"""
from __future__ import annotations

import argparse
import socket
import threading


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:  # noqa: BLE001
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:  # noqa: BLE001
                pass


def _handle(conn: socket.socket, pin: dict[str, str]) -> None:
    try:
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = conn.recv(4096)
            if not chunk:
                conn.close()
                return
            head += chunk
            if len(head) > 65536:
                conn.close()
                return
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            conn.sendall(b"HTTP/1.1 405 Only CONNECT\r\n\r\n")
            conn.close()
            return
        host, _, port_s = parts[1].partition(":")
        port = int(port_s or 443)
        target = pin.get(host, host)
        up = socket.create_connection((target, port), timeout=20)
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        threading.Thread(target=_pipe, args=(conn, up), daemon=True).start()
        threading.Thread(target=_pipe, args=(up, conn), daemon=True).start()
    except Exception as e:  # noqa: BLE001
        print(f"  [失败] {type(e).__name__}: {e}", flush=True)
        try:
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        except Exception:  # noqa: BLE001
            pass
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", required=True, help="github.com 要打到的 IP")
    ap.add_argument("--port", type=int, default=18080)
    args = ap.parse_args()

    pin = {"github.com": args.ip, "www.github.com": args.ip}
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(50)
    print(f"CONNECT 转发已启动 127.0.0.1:{args.port}  github.com -> {args.ip}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_handle, args=(conn, pin), daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
