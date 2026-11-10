#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 3 周 · 大模型真实调用验证
================================================
目的：把「链路能跑」升级为「有真实调用证据」。跑完输出
  python/outputs/llm_eval.json   机器可读的逐题结果
  python/outputs/llm_eval.md     可直接贴进报告的表格

评测方式（刻意做成双轨，避免自己给自己打分）：
  1) 参考 SQL 轨：每道题配一条人工核定的参考 SQL，取它的结果与模型生成的 SQL 结果比对
  2) 人工复核轨：把模型生成的 SQL 原样抄进报告，由人判断语义是否正确
     因为「结果碰巧对上」不等于「SQL 写对了」（例如时间过滤漏了但当天恰好是全年唯一极值）

判定口径：
  执行成功 = SQL 通过白名单校验并在 Hive 上跑通
  数值一致 = 行数相同，且所有数值在 1% 或 0.01 的容差内一一对应
  语义正确 = 人工复核（脚本留空，跑完由人填）

用法：
  python python/validate_llm.py
  python python/validate_llm.py --insight    # 额外跑 AI 洞察的真实调用
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import llm_client  # noqa: E402
import nl2sql  # noqa: E402
from hive_runner import run_sql  # noqa: E402

OUT_JSON = ROOT / "python" / "outputs" / "llm_eval.json"
OUT_MD = ROOT / "python" / "outputs" / "llm_eval.md"

# 题目 / 参考 SQL / 备注（备注写清这题在考什么，人工复核时对着看）
CASES = [
    {
        "q": "2024 年全年 GMV 是多少？",
        "ref": "SELECT ROUND(SUM(gmv), 2) AS gmv FROM ydsh_dws.dws_sales_day",
        "probe": "全表聚合，考表选择与聚合函数",
    },
    {
        "q": "客单价是多少？",
        "ref": "SELECT metric_value AS aov FROM ydsh_ads.ads_kpi_summary WHERE metric_key = 'aov'",
        "probe": "考 KPI 宽表的 metric_key 过滤；易错点是自己去算 SUM/COUNT 导致口径不一致",
    },
    {
        "q": "销量最高的前 5 个品类有哪些？",
        "ref": ("SELECT rank_no, category, gmv FROM ydsh_ads.ads_category_top10 "
                "WHERE rank_no <= 5 ORDER BY rank_no"),
        "probe": "考 TopN 语义；易错点是拿 DWS 明细自己排而不是用 ADS 现成结果",
    },
    {
        "q": "各渠道的 GMV 占比是多少？",
        "ref": ("SELECT channel, gmv_share FROM ydsh_ads.ads_channel_contribution "
                "ORDER BY gmv DESC"),
        "probe": "考占比字段；易错点是自己算分母导致与 ADS 口径不符",
    },
    {
        "q": "复购率是多少？",
        "ref": ("SELECT metric_value AS repeat_rate FROM ydsh_ads.ads_kpi_summary "
                "WHERE metric_key = 'repeat_rate'"),
        "probe": "考指标别名；易错点是编一个自己没有的指标口径",
    },
    {
        "q": "广东省全年销售额是多少？",
        "ref": ("SELECT ROUND(SUM(gmv), 2) AS gmv FROM ydsh_dws.dws_sales_store_day "
                "WHERE province LIKE '%广东%'"),
        "probe": "考维度过滤；易错点是写成 = '广东' 而非 LIKE，或漏掉省字",
    },
    {
        "q": "最近 7 天每天的下单量",
        "ref": ("SELECT stat_date, order_cnt FROM ydsh_dws.dws_sales_day "
                "ORDER BY stat_date DESC LIMIT 7"),
        "probe": "考时间序列 + 排序 + 限行三件事同时成立；这是规则兜底最容易翻车的题",
    },
    {
        "q": "2024 年 11 月 6 日的 GMV 是多少？",
        "ref": ("SELECT ROUND(SUM(gmv), 2) AS gmv FROM ydsh_dws.dws_sales_day "
                "WHERE stat_date = '2024-11-06'"),
        "probe": "考日期字面量格式；易错点是写成 2024/11/06 导致查出 0 行",
    },
]


def numerics(rows: list[dict]) -> list[float]:
    """把结果里所有能转成数字的值抽出来，用于顺序无关的比对。"""
    out = []
    for r in rows:
        for v in r.values():
            if v is None or v == "":
                continue
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
    return out


def same_result(a: list[dict], b: list[dict], rel: float = 0.01, abs_: float = 0.01) -> dict:
    """两条 SQL 结果的比对，返回严格/宽松两个判据。

    严格：行数相同，且数值集合一一对应（列集合必须一致）
    宽松：行数相同，且参考结果的每个数值都能在模型结果里找到对应值
          —— 模型多带一列（如多查了 gmv）不该算错，那是列选多了，不是算错了
    两个判据都留着，是因为它们回答的是不同问题：「算得一样吗」和「够不够用」。
    """
    if len(a) != len(b):
        return {"strict": False, "loose": False,
                "note": f"行数不同：模型 {len(a)} 行 / 参考 {len(b)} 行"}
    na, nb = sorted(numerics(a)), sorted(numerics(b))

    def close(x: float, y: float) -> bool:
        return abs(x - y) <= max(abs_, abs(y) * rel)

    strict = len(na) == len(nb) and all(close(x, y) for x, y in zip(na, nb))
    # 宽松：参考的每个值都要被覆盖（一一消耗，避免同一个值重复抵账）
    pool = list(na)
    missing = []
    for y in nb:
        for i, x in enumerate(pool):
            if close(x, y):
                pool.pop(i)
                break
        else:
            missing.append(y)
    loose = not missing

    if strict:
        note = "一致"
    elif loose:
        note = f"宽松一致（模型多返回 {len(na) - len(nb)} 个数值，列选多了）"
    elif missing:
        note = f"缺值：参考中的 {missing[0]:,.2f} 在模型结果里找不到（模型 {len(na)} 值 / 参考 {len(nb)} 值）"
    else:
        note = "数值不符"
    return {"strict": strict, "loose": loose, "note": note}


def run_case(case: dict) -> dict:
    print(f"\n[{case['q']}]")
    res = nl2sql.ask(case["q"], offline=False, verbose=False)
    rec = {
        "question": case["q"],
        "probe": case["probe"],
        "sql": res.get("sql"),
        "ok": res.get("ok", False),
        "error": res.get("error"),
        "mode": res["trace"]["mode"],
        "attempts": len(res["trace"]["attempts"]),
        "rows": len(res.get("rows") or []),
        # 存一份结果行，便于 --reuse 时重算判据而不必再调模型
        "result_rows": (res.get("rows") or [])[:30],
        "ref_rows_data": None,
    }

    # 调用侧统计
    tokens = [a.get("tokens") for a in res["trace"]["attempts"] if a.get("tokens")]
    elapsed = [a.get("elapsed") for a in res["trace"]["attempts"] if a.get("elapsed")]
    rec["prompt_tokens"] = sum(tokens) if tokens else None
    rec["elapsed"] = round(sum(elapsed), 2) if elapsed else None
    rec["model"] = next((a.get("model") for a in res["trace"]["attempts"] if a.get("model")), None)

    # 参考 SQL 轨
    try:
        ref_rows = run_sql(case["ref"])
        rec["ref_rows"] = len(ref_rows)
        rec["ref_rows_data"] = ref_rows[:30]
        if res.get("ok"):
            v = same_result(res["rows"], ref_rows)
            rec["strict"], rec["loose"], rec["match_note"] = v["strict"], v["loose"], v["note"]
        else:
            rec["strict"] = rec["loose"] = False
            rec["match_note"] = "模型 SQL 未跑通"
    except Exception as e:  # noqa: BLE001
        rec["strict"] = rec["loose"] = None
        rec["match_note"] = f"参考 SQL 本身出错：{e}"

    flag = "✔" if rec["ok"] else "✘"
    print(f"  {flag} 执行{'成功' if rec['ok'] else '失败'} | {rec['match_note']} | "
          f"{rec.get('elapsed')}s | {rec.get('prompt_tokens')} tokens | 模型 {rec.get('model')}")
    return rec


def run_insight() -> dict | None:
    """跑一次 AI 洞察的真实调用，只统计调用侧指标。"""
    print("\n[AI 洞察 · 2024-11-06 案例]")
    import subprocess
    t0 = datetime.now()
    log = ROOT / ".tmp" / "w3" / "insight_llm.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as f:
        subprocess.run([sys.executable, str(ROOT / "python" / "ai_insight.py"),
                        "--date", "2024-11-06"],
                       cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT, timeout=900)
    tail = log.read_text(encoding="utf-8", errors="replace")[-800:]
    print("  已执行，尾部输出：")
    for line in tail.strip().splitlines()[-6:]:
        print("   ", line)
    return {"case": "2024-11-06", "log_tail": tail, "wall_sec": round((datetime.now() - t0).total_seconds(), 1)}


def load_review() -> dict:
    """人工复核结论单独存一个文件，重跑脚本不会被覆盖。

    结构：{"1": {"verdict": "正确", "note": "..."}}，键是题号（从 1 开始）。
    """
    p = ROOT / "python" / "outputs" / "llm_eval_review.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def to_markdown(records: list[dict], insight: dict | None, meta: dict) -> str:
    review = load_review()
    ok_n = sum(1 for r in records if r["ok"])
    strict_n = sum(1 for r in records if r.get("strict"))
    loose_n = sum(1 for r in records if r.get("loose"))
    tok = sum(r["prompt_tokens"] for r in records if r.get("prompt_tokens"))
    el = sum(r["elapsed"] for r in records if r.get("elapsed"))
    judged = [review.get(str(i)) for i in range(1, len(records) + 1)]
    right_n = sum(1 for j in judged if j and j.get("verdict") == "正确")

    L = []
    L.append("# 大模型真实调用验证结果\n")
    L.append(f"- 执行时间：{meta['ts']}")
    L.append(f"- 服务商 / 模型：{meta['provider']} / {meta['model']}")
    L.append(f"- 题目数：{len(records)}　执行成功：{ok_n}/{len(records)}　"
             f"严格一致：{strict_n}/{len(records)}　宽松一致：{loose_n}/{len(records)}"
             + (f"　人工判定正确：{right_n}/{len(records)}" if right_n else ""))
    L.append(f"- 累计 prompt tokens：{tok:,}　累计耗时：{el:.2f}s　"
             f"平均单题：{el / max(len(records), 1):.2f}s\n")
    L.append("> 三个判据回答三个不同问题："
             "**执行成功**=SQL 能在 Hive 上跑通；"
             "**严格一致**=数值集合与参考 SQL 完全对应；"
             "**宽松一致**=参考结果的数值都被覆盖（允许多查列）。"
             "**都不是「语义正确」**——语义由人工复核，见最后一列。\n")

    L.append("## 逐题结果\n")
    L.append("| # | 问题 | 执行 | 严格 | 宽松 | 耗时(s) | tokens | 人工复核 |")
    L.append("|---|------|------|------|------|---------|--------|----------|")
    for i, r in enumerate(records, 1):
        j = review.get(str(i)) or {}
        verdict = j.get("verdict") or "（待填）"
        L.append(f"| {i} | {r['question']} | {'成功' if r['ok'] else '失败'} | "
                 f"{'✔' if r.get('strict') else '—'} | {'✔' if r.get('loose') else '✘'} | "
                 f"{r.get('elapsed') or '-'} | {r.get('prompt_tokens') or '-'} | {verdict} |")

    L.append("\n## 模型生成的 SQL 与比对说明\n")
    for i, r in enumerate(records, 1):
        j = review.get(str(i)) or {}
        L.append(f"**{i}. {r['question']}**　`{r['probe']}`\n")
        if r["ok"]:
            L.append("```sql\n" + (r["sql"] or "").strip() + "\n```")
            L.append(f"结果 {r['rows']} 行，与参考 SQL：{r['match_note']}；"
                     f"重试 {r['attempts'] - 1} 次")
            if j.get("note"):
                L.append(f"\n人工复核：**{j.get('verdict', '')}** — {j['note']}")
            L.append("")
        else:
            L.append("```sql\n" + (r["sql"] or "（未生成）").strip() + "\n```")
            L.append(f"失败原因：`{r['error']}`\n")

    if insight:
        L.append("## AI 洞察调用\n")
        L.append(f"- 案例：{insight['case']}，端到端耗时 {insight['wall_sec']}s（含 Hive 查询）")
        L.append("- 完整输出见 `python/outputs/ai_insight.md`\n")
        L.append("```\n" + insight["log_tail"].strip()[-500:] + "\n```")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="大模型真实调用验证")
    ap.add_argument("--insight", action="store_true", help="额外跑 AI 洞察真实调用")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题，调试用")
    ap.add_argument("--reuse", action="store_true",
                    help="不重新调用模型，用上次的 llm_eval.json 重渲染报表（改了人工复核时用）")
    args = ap.parse_args()

    if args.reuse:
        if not OUT_JSON.exists():
            print("没有找到上次的结果，先不带 --reuse 跑一次。")
            return 2
        old = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        # 用存下来的结果行重算判据，这样调整判据口径不必再花 token
        for r in old["records"]:
            if r.get("result_rows") and r.get("ref_rows_data"):
                v = same_result(r["result_rows"], r["ref_rows_data"])
                r["strict"], r["loose"], r["match_note"] = v["strict"], v["loose"], v["note"]
        OUT_JSON.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
        OUT_MD.write_text(to_markdown(old["records"], old.get("insight"), old["meta"]),
                          encoding="utf-8")
        print(f"已按上次结果重渲染 {OUT_MD.relative_to(ROOT)}（未产生新的 API 调用）")
        return 0

    if not llm_client.available():
        print("未配置 LLM_API_KEY，本脚本用于真实调用验证，没有 Key 时无法运行。")
        return 2

    cases = CASES[: args.limit] if args.limit else CASES
    print(f"开始验证，共 {len(cases)} 题，模型 {nl2sql.llm_client.MODEL}")

    records = []
    for c in cases:
        try:
            records.append(run_case(c))
        except Exception as e:  # noqa: BLE001
            print(f"  ✘ 本题异常：{e}")
            records.append({"question": c["q"], "probe": c["probe"], "ok": False,
                            "sql": None, "error": str(e)[:200], "rows": 0,
                            "strict": False, "loose": False,
                            "match_note": "异常中断", "attempts": 0})

    insight = run_insight() if args.insight else None

    meta = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "provider": llm_client.PROVIDER,
            "model": next((r["model"] for r in records if r.get("model")),
                          llm_client.MODEL)}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"meta": meta, "records": records, "insight": insight},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_MD.write_text(to_markdown(records, insight, meta), encoding="utf-8")

    ok_n = sum(1 for r in records if r["ok"])
    strict_n = sum(1 for r in records if r.get("strict"))
    loose_n = sum(1 for r in records if r.get("loose"))
    print(f"\n完成：执行成功 {ok_n}/{len(records)}，严格一致 {strict_n}/{len(records)}，"
          f"宽松一致 {loose_n}/{len(records)}")
    print(f"结果已写入 {OUT_MD.relative_to(ROOT)} 与 {OUT_JSON.relative_to(ROOT)}")
    print("人工复核请填 python/outputs/llm_eval_review.json，重跑本脚本不会覆盖它。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
