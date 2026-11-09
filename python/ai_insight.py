#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 3 周 · 子任务 5：AI 智能洞察与自动报告
==========================================
做三件事：
  1) 从 DWS 拉日粒度序列，用「相对 7 日均线的偏离 + 稳健 z 分数」定位异常日；
  2) 对异常日做量价分解（GMV = 有效订单量 × 客单价），拆出「量效应」「价效应」，
     再按渠道 / 品类做贡献度归因，得到一份纯数据的事实清单；
  3) 把事实清单交给大模型写归因叙述；没有配 Key 时走模板兜底。

为什么先做分解再让模型写：
  大模型擅长组织语言、不擅长算数。把「量效应 -29,158 / 价效应 -7,794」这类
  已经算准的中间量喂进去，模型只负责解释，能显著降低它编造数字的空间。

用法：
  python python/ai_insight.py                    # 自动找最严重的一天
  python python/ai_insight.py --date 2024-11-06  # 指定日期
  python python/ai_insight.py --offline          # 不走大模型
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hive_runner
import llm_client

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "python" / "outputs"
BASE_WIN = 30  # 基线窗口：异常日前 30 天（不含当日）


# ---------------------------------------------------------------- 取数
def load_daily() -> list[dict]:
    rows = hive_runner.run_sql(
        "SELECT stat_date, order_cnt, paid_order_cnt, cancel_order_cnt, user_cnt, "
        "gmv, aov, cancel_rate FROM ydsh_dws.dws_sales_day ORDER BY stat_date",
        max_rows=1000,  # 366 天全量，超过默认安全上限
    )
    out = []
    for r in rows:
        out.append({
            "d": str(r["stat_date"]),
            "order_cnt": int(r["order_cnt"]),
            "paid": int(r["paid_order_cnt"]),
            "cancel": int(r["cancel_order_cnt"]),
            "users": int(r["user_cnt"]),
            "gmv": float(r["gmv"]),
            "aov": float(r["aov"]),
            "cancel_rate": float(r["cancel_rate"]),
        })
    return out


def _all_scored(daily: list[dict]) -> list[dict]:
    """给每天的 GMV 打上「相对 7 日均线偏离」与「MAD 稳健 z 分数」。"""
    gmvs = [r["gmv"] for r in daily]
    med = statistics.median(gmvs)
    mad = statistics.median([abs(g - med) for g in gmvs]) or 1e-9
    scored = []
    for i, r in enumerate(daily):
        # 前 BASE_WIN 天基线窗口不足，选出来也没法解释，直接跳过
        if i < BASE_WIN:
            continue
        ma7 = statistics.mean(gmvs[i - 7:i])
        dev = (r["gmv"] - ma7) / ma7
        rz = 0.6745 * (r["gmv"] - med) / mad
        scored.append({**r, "ma7": ma7, "dev": dev, "rz": rz, "score": abs(rz)})
    return scored


def find_anomaly(daily: list[dict], direction: str = "drop") -> dict:
    """用 7 日均线偏离 + MAD 稳健 z 分数挑出最异常的一天。

    direction="drop" 只找下跌（任务要求的是「GMV 突降」归因），
    "spike" 只找上涨，"any" 按绝对偏离。
    """
    scored = _all_scored(daily)
    if direction == "drop":
        pool = [x for x in scored if x["dev"] < 0] or scored
        return min(pool, key=lambda x: x["dev"])
    if direction == "spike":
        pool = [x for x in scored if x["dev"] > 0] or scored
        return max(pool, key=lambda x: x["dev"])
    return max(scored, key=lambda x: x["score"])


# ---------------------------------------------------------------- 归因
def decompose(daily: list[dict], target: str) -> dict:
    """量价分解：ΔGMV ≈ 量效应 + 价效应（交叉项并入两者，误差可忽略）。"""
    idx = next(i for i, r in enumerate(daily) if r["d"] == target)
    cur = daily[idx]

    lo = max(0, idx - BASE_WIN)
    hist = daily[lo:idx]
    base_paid = statistics.mean(r["paid"] for r in hist)
    base_aov = statistics.mean(r["aov"] for r in hist)
    base_gmv = statistics.mean(r["gmv"] for r in hist)

    qty_eff = (cur["paid"] - base_paid) * base_aov       # 量效应
    price_eff = cur["paid"] * (cur["aov"] - base_aov)    # 价效应
    delta = cur["gmv"] - base_gmv

    return {
        "date": target,
        "gmv": cur["gmv"],
        "base_gmv": round(base_gmv, 2),
        "delta": round(delta, 2),
        "delta_pct": round(delta / base_gmv * 100, 2),
        "paid": cur["paid"],
        "base_paid": round(base_paid, 2),
        "aov": cur["aov"],
        "base_aov": round(base_aov, 2),
        "order_cnt": cur["order_cnt"],
        "cancel": cur["cancel"],
        "cancel_rate": round(cur["cancel_rate"] * 100, 2),
        "base_cancel_rate": round(
            statistics.mean(r["cancel_rate"] for r in hist) * 100, 2
        ),
        "qty_effect": round(qty_eff, 2),
        "price_effect": round(price_eff, 2),
        "residual": round(delta - qty_eff - price_eff, 2),
        "base_win": f"{hist[0]['d']} ~ {hist[-1]['d']}",
    }


def _window(daily: list[dict], target: str) -> tuple[str, str]:
    """返回基线窗口 [起, 止]：异常日前 BASE_WIN 天，不含当日。"""
    idx = next(i for i, r in enumerate(daily) if r["d"] == target)
    lo = max(0, idx - BASE_WIN)
    return daily[lo]["d"], daily[idx - 1]["d"] if idx > 0 else daily[idx]["d"]


def _attr(sql_day: str, sql_base: str, key: str, val: str, target: str,
          daily: list[dict]) -> list[dict]:
    """通用维度归因：当日值 vs 基线窗口均值。

    基线必须用同一窗口的均值（SUM/COUNT），不能用「累计/天数」近似——
    那样天数一旦不匹配（如年初）会算出离谱的基线。
    """
    lo, hi = _window(daily, target)
    n_days = (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1

    day_rows = hive_runner.run_sql(sql_day.format(t=target))
    base_rows = hive_runner.run_sql(sql_base.format(lo=lo, hi=hi))
    base_sum = {r[key]: float(r[val]) for r in base_rows}

    out = []
    for r in day_rows:
        k = r[key]
        cur = float(r[val])
        base = base_sum.get(k, 0.0) / n_days if n_days else 0.0
        out.append({
            key: k,
            "gmv": round(cur, 2),
            "base": round(base, 2),
            "delta": round(cur - base, 2),
            "pct": round((cur - base) / base * 100, 1) if base else 0.0,
        })
    return sorted(out, key=lambda x: x["delta"])


def channel_attr(target: str, daily: list[dict]) -> list[dict]:
    return _attr(
        "SELECT channel, SUM(gmv) AS v FROM ydsh_dws.dws_sales_channel_day "
        "WHERE stat_date = '{t}' GROUP BY channel",
        "SELECT channel, SUM(gmv) AS v FROM ydsh_dws.dws_sales_channel_day "
        "WHERE stat_date >= '{lo}' AND stat_date <= '{hi}' GROUP BY channel",
        "channel", "v", target, daily,
    )


def category_attr(target: str, daily: list[dict]) -> list[dict]:
    return _attr(
        "SELECT category, SUM(gmv_alloc) AS v FROM ydsh_dws.dws_sales_category_day "
        "WHERE stat_date = '{t}' GROUP BY category",
        "SELECT category, SUM(gmv_alloc) AS v FROM ydsh_dws.dws_sales_category_day "
        "WHERE stat_date >= '{lo}' AND stat_date <= '{hi}' GROUP BY category",
        "category", "v", target, daily,
    )


# ---------------------------------------------------------------- 叙述
PROMPT = """你是一名零售数据分析师。下面是一次经营异常的**已核算事实**，请基于这些数字写一段归因分析。

要求：
1. 只使用给定数字，禁止编造任何未提供的指标或外部原因；
2. 先给结论（降幅主要来自量还是价），再给证据；
3. 明确指出哪些是数据能支撑的，哪些是数据无法判断的（例如"是否做了活动"数据里没有）；
4. 不要给"建议加强运营"这类空话，如果要给建议，必须指向可验证的动作；
5. 输出 400 字以内，中文，分 3 段，不用标题。

事实：
{facts}
"""


def template_narrative(f: dict, ch: list[dict], cat: list[dict]) -> str:
    """无 Key 时的兜底：把事实按固定逻辑串起来（不是模型效果，仅保证链路可用）。"""
    qty_share = abs(f["qty_effect"]) / (abs(f["qty_effect"]) + abs(f["price_effect"]) or 1)

    # 只把真正下滑的维度算作拖累项；全线上涨时如实说明
    drag_cat = [c for c in cat if c["delta"] < 0][:2]
    drag_ch = [c for c in ch if c["delta"] < 0]
    up_ch = [c for c in ch if c["delta"] >= 0]

    if drag_cat:
        cat_txt = "、".join(
            f"{c['category']}（{c['delta']:,.0f} 元，{c['pct']:+.0f}%）" for c in drag_cat
        )
        cat_sent = f"结构上，{cat_txt} 是主要拖累项。"
    else:
        cat_sent = f"结构上，{len(cat)} 个品类当日全部高于基线均值，下跌并非由单一品类塌陷造成。"

    if drag_ch and not up_ch:
        ch_sent = (
            f"渠道端 {drag_ch[0]['channel']} 降幅最大（{drag_ch[0]['pct']:+.0f}%），"
            f"且 {len(drag_ch)} 个渠道全线下滑，说明不是单一渠道故障，更可能是全站性因素。"
        )
    elif drag_ch:
        ch_sent = (
            f"渠道端 {drag_ch[0]['channel']} 降幅最大（{drag_ch[0]['pct']:+.0f}%），"
            f"但仍有 {len(up_ch)} 个渠道高于基线，下跌呈结构性而非全站性。"
        )
    else:
        ch_sent = f"渠道端 {len(ch)} 个渠道全部高于基线，下跌不发生在渠道层面。"

    if f["cancel_rate"] > f["base_cancel_rate"] + 1:
        bound = (
            f"需要说明的边界：当天取消 {f['cancel']} 单，取消率 {f['cancel_rate']:.1f}%，"
            f"基线 {f['base_cancel_rate']:.1f}%，明显抬升——这是有效订单偏少的直接原因之一。"
            f"但取消原因（缺货 / 支付失败 / 用户主动）本数据集没有字段记录，"
            f"「为什么取消」无法从数据得出，需要业务方补充日志。"
        )
    else:
        bound = (
            f"需要说明的边界：当天取消 {f['cancel']} 单，取消率 {f['cancel_rate']:.1f}%，"
            f"基线 {f['base_cancel_rate']:.1f}%，并没有异常。"
            f"也就是说下跌不是履约或支付环节造成的，问题出在下单之前——"
            f"但流量、曝光、转化率这三项本数据集均未采集，"
            f"因此「为什么没人下单」超出了现有数据的回答能力。"
        )

    return (
        f"{f['date']} 的 GMV 为 {f['gmv']:,.0f} 元，较前 {BASE_WIN} 日均值 "
        f"{f['base_gmv']:,.0f} 元下降 {abs(f['delta_pct']):.1f}%。"
        f"量价分解显示：有效订单量从 {f['base_paid']:.1f} 单降到 {f['paid']} 单，"
        f"贡献量效应 {f['qty_effect']:,.0f} 元；客单价从 {f['base_aov']:,.0f} 元变为 "
        f"{f['aov']:,.0f} 元，贡献价效应 {f['price_effect']:,.0f} 元。"
        f"量效应占绝对贡献的 {qty_share*100:.0f}%，因此这是一次以量为主的下跌。\n\n"
        f"{cat_sent}{ch_sent}\n\n{bound}"
    )


def llm_narrative(facts: str) -> tuple[str, dict]:
    r = llm_client.chat([{"role": "user", "content": PROMPT.format(facts=facts)}],
                        temperature=0.2)
    return r["content"], {"elapsed": r["elapsed"], "tokens": r["prompt_tokens"] + r["completion_tokens"]}


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="指定异常日期 yyyy-MM-dd")
    ap.add_argument("--offline", action="store_true", help="不走大模型")
    ap.add_argument("--direction", default="drop", choices=["drop", "spike", "any"],
                    help="找突降(drop) / 突涨(spike) / 任意(any)")
    args = ap.parse_args()

    daily = load_daily()
    print(f"读入 {len(daily)} 天日粒度数据")

    a = find_anomaly(daily, args.direction)
    target = args.date or a["d"]
    if args.date and target != a["d"]:
        # 手动指定日期时，偏离度要按该日期重算，不能沿用自动选中日的数字
        a = next(x for x in _all_scored(daily) if x["d"] == target)
        print(f"指定日期：{target}（相对 7 日均线偏离 {a['dev']*100:+.1f}%，稳健 z={a['rz']:.2f}）")
    else:
        print(f"异常日：{target}（相对 7 日均线偏离 {a['dev']*100:+.1f}%，稳健 z={a['rz']:.2f}）")

    f = decompose(daily, target)
    ch = channel_attr(target, daily)
    cat = category_attr(target, daily)

    facts = json.dumps(
        {"核心指标": f, "渠道贡献": ch, "品类贡献": cat[:4]},
        ensure_ascii=False, indent=2,
    )

    used_llm = llm_client.available() and not args.offline
    meta = {}
    if used_llm:
        print(f"调用大模型：{llm_client.MODEL}")
        try:
            narrative, meta = llm_narrative(facts)
        except Exception as e:  # 网络/额度问题不应让整个链路挂掉
            print(f"大模型调用失败，转模板兜底：{e}")
            narrative, used_llm = template_narrative(f, ch, cat), False
    else:
        narrative = template_narrative(f, ch, cat)

    # ---------------- 输出 ----------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "insight_facts.json").write_text(
        json.dumps({"target": target, "facts": json.loads(facts),
                    "narrative": narrative, "used_llm": used_llm, "meta": meta},
                   ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [f"# 经营异常归因 · {target}", "", "## 一、事实层（已核算，不含解释）", "",
             "| 指标 | 当日 | 前30日基线 | 差异 |", "| --- | --- | --- | --- |",
             f"| GMV（元） | {f['gmv']:,.0f} | {f['base_gmv']:,.0f} | {f['delta']:,.0f}（{f['delta_pct']:+.1f}%） |",
             f"| 有效订单量（单） | {f['paid']} | {f['base_paid']:.1f} | {f['paid']-f['base_paid']:+.1f} |",
             f"| 客单价（元） | {f['aov']:,.0f} | {f['base_aov']:,.0f} | {f['aov']-f['base_aov']:+,.0f} |",
             f"| 取消率（%） | {f['cancel_rate']:.1f} | {f['base_cancel_rate']:.1f} | {f['cancel_rate']-f['base_cancel_rate']:+.1f} |",
             "", f"量价分解：量效应 {f['qty_effect']:,.0f} 元 ｜ 价效应 {f['price_effect']:,.0f} 元 ｜ 残差 {f['residual']:,.0f} 元", ""]

    lines += ["## 二、渠道贡献（按拖累排序）", "",
              "| 渠道 | 当日 GMV | 日均基线 | 差异 | 幅度 |", "| --- | --- | --- | --- | --- |"]
    for r in ch:
        lines.append(f"| {r['channel']} | {r['gmv']:,.0f} | {r['base']:,.0f} | {r['delta']:,.0f} | {r['pct']:+.1f}% |")

    lines += ["", "## 三、品类贡献（按拖累排序）", "",
              "| 品类 | 当日 GMV | 日均基线 | 差异 | 幅度 |", "| --- | --- | --- | --- | --- |"]
    for r in cat:
        lines.append(f"| {r['category']} | {r['gmv']:,.0f} | {r['base']:,.0f} | {r['delta']:,.0f} | {r['pct']:+.1f}% |")

    src = f"大模型 {llm_client.MODEL}" if used_llm else "规则模板（未配置 API Key 或指定 --offline）"
    lines += ["", f"## 四、归因叙述（来源：{src}）", "", narrative, ""]
    if meta:
        lines += [f"> 调用耗时 {meta['elapsed']:.2f}s，tokens {meta['tokens']}", ""]

    out = OUT_DIR / "ai_insight.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已输出 {out}")
    print(f"归因来源：{src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
