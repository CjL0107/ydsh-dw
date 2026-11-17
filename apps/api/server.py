#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 4 周 · 可视化数据服务（Flask）
================================================
一个进程同时干两件事，是为了让「一键部署」少一个容器：

  /                  ECharts 大屏（静态页）
  /api/overview      整块大屏的数据（一次请求拿全，避免六个面板六次握手）
  /api/<panel>       单面板，答辩时想单独演示某个图可以用
  /api/health        健康检查，Docker healthcheck 也打这个
  /api/refresh       POST，重跑 Hive 查询刷新快照（慢，约 30s）

数据来源：apps/api/data/snapshot.json
  由 refresh_snapshot.py 生成；服务启动时若文件不存在会直接报错退出，
  而不是返回空数据 —— 空数据会让大屏看起来"跑通了但没数"，比报错更难查。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
SNAPSHOT = BASE / "data" / "snapshot.json"
SCREEN_DIR = ROOT / "apps" / "screen"

app = Flask(__name__, static_folder=None)

_state: dict = {"snapshot": None, "loaded_at": 0.0, "mtime": 0.0}
_lock = threading.Lock()


# ----------------------------------------------------------------------
# 快照读取（按文件 mtime 失效，刷新脚本写完就能立刻生效）
# ----------------------------------------------------------------------
def load_snapshot(force: bool = False) -> dict:
    if not SNAPSHOT.exists():
        raise FileNotFoundError(
            f"快照不存在：{SNAPSHOT}\n先执行：python apps/api/refresh_snapshot.py"
        )
    mtime = SNAPSHOT.stat().st_mtime
    with _lock:
        if force or _state["snapshot"] is None or mtime != _state["mtime"]:
            with SNAPSHOT.open(encoding="utf-8") as fh:
                _state["snapshot"] = json.load(fh)
            _state["mtime"] = mtime
            _state["loaded_at"] = time.time()
        return _state["snapshot"]


def _meta() -> dict:
    snap = load_snapshot()
    meta = dict(snap.get("meta", {}))
    meta["served_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta["snapshot_age_seconds"] = round(time.time() - _state["loaded_at"], 1)
    return meta


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
@app.get("/api/health")
def health():
    try:
        snap = load_snapshot()
        meta = snap.get("meta", {})
        return jsonify({
            "status": "ok",
            "backend": "snapshot",
            "generated_at": meta.get("generated_at"),
            "panels": [k for k in snap if k != "meta"],
            "uptime_seconds": round(time.time() - _state["loaded_at"], 1),
        })
    except Exception as e:  # noqa: BLE001
        return jsonify({"status": "error", "detail": str(e)}), 503


@app.get("/api/overview")
def overview():
    snap = load_snapshot()
    body = {k: v for k, v in snap.items() if k != "meta"}
    body["meta"] = _meta()
    return jsonify(body)


@app.get("/api/<panel>")
def panel(panel: str):
    snap = load_snapshot()
    if panel not in snap or panel == "meta":
        return jsonify({"error": f"未知面板：{panel}", "available": list(snap.keys())}), 404
    return jsonify({panel: snap[panel], "meta": _meta()})


@app.post("/api/refresh")
def refresh():
    """重跑查询刷新快照。同步执行（30 秒左右），失败时保留旧快照不动。"""
    token = os.getenv("REFRESH_TOKEN", "")
    if token and request.headers.get("X-Refresh-Token") != token:
        return jsonify({"error": "刷新令牌错误"}), 403
    try:
        from refresh_snapshot import refresh  # 延迟导入，避免启动就依赖 hive

        t0 = time.time()
        meta = refresh()
        load_snapshot(force=True)
        return jsonify({"status": "refreshed", "seconds": round(time.time() - t0, 1), "meta": meta})
    except Exception as e:  # noqa: BLE001
        # 刷新失败不动旧快照：大屏宁可显示 10 分钟前的数，也不要变成空白
        return jsonify({"status": "error", "detail": str(e)[:300]}), 500


# ----------------------------------------------------------------------
# 静态页：大屏
# ----------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(SCREEN_DIR, "index.html")


@app.get("/<path:filename>")
def assets(filename: str):
    return send_from_directory(SCREEN_DIR, filename)


@app.after_request
def add_headers(resp):
    # 大屏与 API 同源部署，允许跨域只为方便本地 Streamlit 调试
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store" if resp.mimetype == "application/json" else "public, max-age=3600"
    return resp


def main() -> None:
    port = int(os.getenv("APP_PORT", "8000"))
    load_snapshot()  # 起服务前先确认快照可读，坏文件当场暴露
    print(f"[api] 大屏 http://127.0.0.1:{port}/   接口 http://127.0.0.1:{port}/api/overview")
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
