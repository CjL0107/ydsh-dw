#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 3 周 · 子任务 3：大模型 API 接入与密钥管理
================================================
设计要点：
  1) Key 只从环境变量读，代码里不出现任何字面量；.env 已被 .gitignore 忽略
  2) 提供 available() 供上层判断「有没有配 Key」，没配就走离线兜底，不至于让整条链路崩掉
  3) 统一 OpenAI 兼容协议，deepseek / 通义 / 智谱 / Moonshot 都能直接换 base_url
  4) 记录每次调用的耗时与 token 数，便于在报告里给出成本量级

用法：
  python python/llm_client.py            # 连通性自检
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").strip()
API_KEY = os.getenv("LLM_API_KEY", "").strip()
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").strip()
MODEL = os.getenv("LLM_MODEL", "deepseek-chat").strip()

# 常见服务商的默认 base_url，方便只改 provider 就切换
DEFAULT_BASE = {
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "moonshot": "https://api.moonshot.cn/v1",
}


def available() -> bool:
    return bool(API_KEY)


def _client():
    from openai import OpenAI
    return OpenAI(api_key=API_KEY, base_url=BASE_URL or DEFAULT_BASE.get(PROVIDER))


def chat(messages: list[dict], temperature: float = 0.0, timeout: int = 60) -> dict:
    """返回 {content, elapsed, prompt_tokens, completion_tokens}"""
    if not available():
        raise RuntimeError("未配置 LLM_API_KEY，请复制 .env.example 为 .env 并填入 Key")

    client = _client()
    t0 = time.time()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        timeout=timeout,
    )
    elapsed = round(time.time() - t0, 2)
    usage = getattr(resp, "usage", None)
    return {
        "content": resp.choices[0].message.content or "",
        "elapsed": elapsed,
        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
        "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
    }


def test_connection() -> int:
    print("=" * 56)
    print("大模型 API 连通性自检")
    print("=" * 56)
    print(f"provider = {PROVIDER}")
    print(f"base_url = {BASE_URL}")
    print(f"model    = {MODEL}")
    print(f"api_key  = {'已配置（长度 %d，末四位 ****%s）' % (len(API_KEY), API_KEY[-4:]) if API_KEY else '未配置'}")

    if not available():
        print("\n[跳过] 未检测到 LLM_API_KEY。")
        print("       配置方式：在仓库根目录执行  cp .env.example .env")
        print("       然后把 .env 里的 LLM_API_KEY 填成真实 Key，再重跑本脚本。")
        return 2

    print("\n发起最小化调用 ...")
    try:
        r = chat([
            {"role": "system", "content": "你是一个简洁的助手，只回答被问到的内容。"},
            {"role": "user", "content": "用一句不超过 20 字的话说明你已就绪。"},
        ])
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}")
        return 1

    print(f"[成功] 耗时 {r['elapsed']}s")
    print(f"       返回：{r['content'].strip()}")
    if r["prompt_tokens"] is not None:
        print(f"       tokens：prompt={r['prompt_tokens']}, completion={r['completion_tokens']}")
    return 0


if __name__ == "__main__":
    sys.exit(test_connection())
