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
MODEL = os.getenv("LLM_MODEL", "deepseek-flash").strip()

# 服务商的模型名会变（如 deepseek-chat 曾下线），配置的模型名不可用时按顺序降级，
# 避免一个过期字符串让整条链路报 404。
MODEL_CANDIDATES = [MODEL, "deepseek-flash", "deepseek-v4-pro", "deepseek-chat"]

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


def _explain(exc: Exception) -> str:
    """把 SDK 异常翻译成能直接照做的提示，别让人对着 402 发愣。"""
    msg = str(exc)
    low = msg.lower()
    if "402" in msg or "insufficient balance" in low:
        return ("账户余额不足（402）。Key 本身有效、鉴权已通过，"
                "去 https://platform.deepseek.com/top_up 充值后重跑即可。")
    if "401" in msg or "invalid api key" in low or "authentication" in low:
        return "Key 无效或已被删除（401）。请到控制台重新创建 API key，确认复制完整。"
    if "404" in msg or "model not found" in low or "does not exist" in low:
        return "模型名不存在（404）。运行 python python/llm_client.py --models 列出可用模型。"
    if "429" in msg or "rate limit" in low:
        return "触发限流（429）。降低并发或稍后重试。"
    if "timeout" in low or "timed out" in low:
        return "请求超时。检查网络或代理，或调大 timeout。"
    return f"{type(exc).__name__}: {msg}"


def _is_model_missing(exc: Exception) -> bool:
    low = str(exc).lower()
    return "404" in str(exc) or "model not found" in low or "does not exist" in low


def chat(messages: list[dict], temperature: float = 0.0, timeout: int = 60) -> dict:
    """返回 {content, elapsed, prompt_tokens, completion_tokens, model}

    配置的模型名不可用时自动降级到候选列表里的下一个，并把实际用的模型名带回去，
    这样报告里记的是「真实调用的是哪个模型」，不是「我配的是哪个」。
    """
    if not available():
        raise RuntimeError("未配置 LLM_API_KEY，请复制 .env.example 为 .env 并填入 Key")

    client = _client()
    last: Exception | None = None
    seen: set[str] = set()
    for name in MODEL_CANDIDATES:
        if not name or name in seen:
            continue
        seen.add(name)
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=name,
                messages=messages,
                temperature=temperature,
                timeout=timeout,
            )
        except Exception as e:
            last = e
            if _is_model_missing(e):
                continue
            raise
        elapsed = round(time.time() - t0, 2)
        usage = getattr(resp, "usage", None)
        return {
            "content": resp.choices[0].message.content or "",
            "elapsed": elapsed,
            "model": name,
            "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
            "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
        }
    raise last if last else RuntimeError("没有可用的模型名")


def list_models() -> list[str]:
    """探一遍候选模型，返回真正可用的那些（每次只消耗 4 个 token）。"""
    ok = []
    for name in dict.fromkeys(m for m in MODEL_CANDIDATES if m):
        try:
            _client().chat.completions.create(
                model=name, messages=[{"role": "user", "content": "hi"}],
                max_tokens=4, temperature=0, timeout=20,
            )
            ok.append(name)
            print(f"  ✔ {name}")
        except Exception as e:
            print(f"  ✘ {name}: {_explain(e)}")
    return ok


def test_connection() -> int:
    print("=" * 56)
    print("大模型 API 连通性自检")
    print("=" * 56)
    print(f"provider = {PROVIDER}")
    print(f"base_url = {BASE_URL}")
    print(f"model    = {MODEL}")
    print("api_key   = " + ("已配置（长度 %d，末四位 ****%s）" % (len(API_KEY), API_KEY[-4:]) if API_KEY else "未配置"))
    print(f"候选模型 = {', '.join(dict.fromkeys(m for m in MODEL_CANDIDATES if m))}")

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
        print(f"[失败] {_explain(e)}")
        return 1

    print(f"[成功] 耗时 {r['elapsed']}s  实际使用模型 {r['model']}")
    print(f"       返回：{r['content'].strip()}")
    if r["prompt_tokens"] is not None:
        print(f"       tokens：prompt={r['prompt_tokens']}, completion={r['completion_tokens']}")
    return 0


if __name__ == "__main__":
    if "--models" in sys.argv:
        print("逐个探测候选模型可用性 ...")
        good = list_models()
        print(f"\n可用模型：{good or '（无）'}")
        sys.exit(0 if good else 1)
    sys.exit(test_connection())
