#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
浏览器截图小工具（开发/验收用，不属于运行时依赖）
================================================
为什么不直接用 `chrome --headless --screenshot`：
    Streamlit 是 websocket 驱动的，页面 shell 先返回 200，
    真正的内容要等前端连上后才渲染。命令行截图拿到的是骨架屏，
    看起来像"应用挂了"，其实只是没等够。

用法：
    python tools/ui_shot.py --url http://127.0.0.1:8501 --out a.png --wait 4000
    python tools/ui_shot.py --url http://127.0.0.1:8501 --out b.png \
        --ask "各渠道 GMV 占比" --wait 25000
"""
from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--wait", type=int, default=3000, help="打开后额外等待毫秒数")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--ask", help="在 Streamlit 输入框里提问并回车")
    ap.add_argument("--full", action="store_true", help="整页截图")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": args.width, "height": args.height},
                                device_scale_factor=1)
        page.goto(args.url, wait_until="load", timeout=60000)
        page.wait_for_timeout(args.wait)

        if args.ask:
            box = page.locator("textarea").first
            box.wait_for(state="visible", timeout=20000)
            box.fill(args.ask)
            box.press("Enter")
            # 等到"生成中"结束：按钮/输入框恢复可交互说明一轮跑完
            page.wait_for_timeout(2000)
            for _ in range(40):
                if page.locator('[data-testid="stSpinner"]').count() == 0:
                    break
                page.wait_for_timeout(1000)
            page.wait_for_timeout(2500)

        page.screenshot(path=args.out, full_page=args.full)
        print(f"已保存 {args.out}")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
