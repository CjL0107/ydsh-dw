#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Superset 仪表板截图（验收用，不属于运行时依赖）
================================================
为什么要单独一个脚本：
    Superset 的仪表板默认需要登录，`ui_shot.py` 只做 goto，抓到的是登录页。
    这里先走表单登录拿 session cookie（顺手拿到 CSRF token），再打开
    /superset/dashboard/<slug>/ 等图表渲染完再截图。

    JWT 登录在这里不能用：JWT 场景下 g.user 解析不出来，读接口会返回空列表，
    仪表板打开是 6 个空框 —— 这也是 provision.py 坚持用表单登录的原因。

用法：
    python tools/superset_shot.py --slug ydsh-sales-overview \
        --out docs/screenshots/第4周_Superset仪表板.png --wait 12000

账号从环境变量取（SUPERSET_USER / SUPERSET_PASSWORD），默认 admin/admin。
"""
from __future__ import annotations

import argparse
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.getenv("SUPERSET_BASE", "http://127.0.0.1:8088")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--wait", type=int, default=12000, help="图表渲染额外等待毫秒数")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--full", action="store_true", help="整页截图")
    a = ap.parse_args()

    user = os.getenv("SUPERSET_USER", "admin")
    pwd = os.getenv("SUPERSET_PASSWORD", "admin")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": a.width, "height": a.height},
                                device_scale_factor=1)
        page.goto(f"{BASE}/login/", wait_until="load", timeout=60000)
        page.fill('input[name="username"]', user)
        page.fill('input[name="password"]', pwd)
        # 登录页的提交按钮选择器在各版本间变过，直接回车最稳
        page.press('input[name="password"]', "Enter")
        page.wait_for_url(lambda u: "/login" not in u, timeout=30000)
        print(f"[shot] 登录成功 -> {page.url}")

        page.goto(f"{BASE}/superset/dashboard/{a.slug}/", wait_until="load", timeout=60000)
        page.wait_for_timeout(a.wait)          # 等 ECharts/SVG 画完
        page.screenshot(path=a.out, full_page=a.full)
        print(f"[shot] 已保存 {a.out}")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
