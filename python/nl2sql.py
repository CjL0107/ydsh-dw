#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 3 周 · 子任务 4：NL2SQL 查询接口
================================================
链路：自然语言 → Prompt（Schema + Few-shot + 约束）→ 大模型生成 SQL
      → 安全校验 → 执行 → 结果 → （可选）自然语言小结

三个关键设计：
  1) Schema 注入：把 Hive 现抽的库表字段拼进 Prompt，模型不用猜表名
  2) SQL 白名单校验：只放行 SELECT，且只能引用授权表，防止模型生成 DROP 之类
  3) 失败自愈：第一次 SQL 报错时，把报错原文回灌给模型让它改一版，最多重试 2 次
     —— 这一步显著提升可用性，实测能救回「字段名拼错」「Hive 不支持的函数」两类错误

用法：
  python python/nl2sql.py -q "2024 年总 GMV 是多少"
  python python/nl2sql.py -q "销量最高的前 5 个品类" --explain
  python python/nl2sql.py --offline -q "广东省 2024 年销售额"   # 无 Key 时演示链路

无 LLM_API_KEY 时自动降级为「规则兜底」，仅用于验证链路与 SQL 执行，
不代表大模型效果——评估报告里会把两者分开统计。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import llm_client
from hive_runner import SqlRejected, run_sql, validate_sql

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_JSON = ROOT / "python" / "outputs" / "schema.json"
DIM_JSON = ROOT / "python" / "outputs" / "dim_values.json"
LOG_JSONL = ROOT / "python" / "outputs" / "nl2sql_log.jsonl"

ALLOWED_TABLES = {
    "ydsh_dws.dws_sales_day", "ydsh_dws.dws_sales_category_day",
    "ydsh_dws.dws_sales_store_day", "ydsh_dws.dws_sales_channel_day",
    "ydsh_dws.dws_sales_week", "ydsh_dws.dws_sales_month",
    "ydsh_dws.dws_user_order_agg",
    "ydsh_ads.ads_kpi_summary", "ydsh_ads.ads_sales_daily_report",
    "ydsh_ads.ads_category_top10", "ydsh_ads.ads_user_value_segmentation",
    "ydsh_ads.ads_channel_contribution", "ydsh_ads.ads_province_top10",
    "ydsh_ads.ads_store_top10",
}

FEW_SHOT = [
    {
        "q": "2024 年广东省的销售额是多少？",
        "sql": "SELECT ROUND(SUM(gmv), 2) AS gmv FROM ydsh_dws.dws_sales_store_day WHERE province = '广东省'",
    },
    {
        "q": "销量最高的前 5 个品类是哪些？",
        "sql": ("SELECT rank_no, category, gmv FROM ydsh_ads.ads_category_top10 "
                "WHERE rank_no <= 5 ORDER BY rank_no"),
    },
    {
        "q": "2024 年 6 月的 GMV 和环比是多少？",
        "sql": ("SELECT year_month, gmv, mom_rate FROM ydsh_dws.dws_sales_month "
                "WHERE year_month = '2024-06'"),
    },
    {
        "q": "各渠道的销售额占比如何？",
        "sql": ("SELECT channel, gmv, gmv_share FROM ydsh_ads.ads_channel_contribution "
                "ORDER BY gmv DESC"),
    },
]

SYSTEM_PROMPT = """你是 Hive SQL 专家，负责把业务问题翻译成可执行的 Hive SQL。

硬性约束（违反即视为错误）：
1. 只输出一条 SELECT 查询，不要 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE。
2. 只能使用下面列出的表，表名必须带库名前缀（如 ydsh_dws.dws_sales_day）。
3. 字段名必须来自给定 Schema，不要臆造。
4. 金额用 ROUND(x, 2)，百分比字段已是百分数（如 13.83 表示 13.83%）。
5. 时间字段是字符串 'yyyy-MM-dd'，按日期筛选用 BETWEEN 或 LIKE，不要用 date() 函数。
6. 数据库里没有 2023 年数据，所有时间条件只能落在 2024 年。
7. 只输出 SQL 本身，不要解释、不要 Markdown 代码块。
8. 维度列做等值匹配时，必须逐字使用 {dims} 一节给出的原始取值。
   例如库里存的是「广东省」，写 WHERE province = '广东' 会查出空结果。

{examples}

{dims}

可用表结构如下：
{schema}
"""


def load_schema_text() -> str:
    if not SCHEMA_JSON.exists():
        raise SystemExit("未找到 schema.json，请先运行： python python/nl2sql_schema.py")
    data = json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))
    lines = []
    for db, tables in data["databases"].items():
        for t, info in tables.items():
            fq = f"{db}.{t}"
            if fq not in ALLOWED_TABLES:
                continue
            lines.append(f"\n表 {fq} —— {info['note']}")
            parts = []
            for c in info["columns"]:
                cm = c["comment"] or ""
                parts.append(f"{c['name']}({c['type']})" + (f"：{cm}" if cm else ""))
            lines.append("  字段：" + "；".join(parts))
    return "\n".join(lines)


def load_dim_text() -> str:
    """维度取值文本。没有 dim_values.json 时返回空串（降级为只有 Schema）。"""
    if not DIM_JSON.exists():
        print(f"[警告] 未找到 {DIM_JSON.name}，Prompt 不含维度取值，"
              f"等值匹配易出错。请先运行 python python/nl2sql_schema.py")
        return ""
    data = json.loads(DIM_JSON.read_text(encoding="utf-8"))
    lines = ["维度实际取值（WHERE 等值匹配必须逐字照抄，不要简化成简称）："]
    for name, d in data["dims"].items():
        vals = "、".join(f"'{v}'" for v in d["values"])
        tail = f" 等共 {d['total']} 个" if d.get("truncated") else ""
        lines.append(f"- {d['table']}.{d['column']}（{d['note']}）：{vals}{tail}")
    return "\n".join(lines)


def load_dim_map() -> dict[str, dict]:
    """列名 -> 合法取值集合，供结果侧校验用。"""
    if not DIM_JSON.exists():
        return {}
    data = json.loads(DIM_JSON.read_text(encoding="utf-8"))
    return {d["column"]: d for d in data["dims"].values()}


def check_dim_literals(sql: str) -> list[dict]:
    """检查 SQL 里维度列的等值字面量是否真是库里的取值。

    这是**结果侧**防线：生成侧 Prompt 已经注入了取值，但模型仍可能写简称
    （实测把「广东省」写成「广东」）。这类错误执行不报错、只返回空结果，
    比语法错误更难发现，所以必须在执行前拦一道。
    LIKE 不检查——模糊匹配写「广东」能命中「广东省」，本来就没错。
    """
    dims = load_dim_map()
    if not dims:
        return []
    issues = []
    for col, d in dims.items():
        valid = set(d["values"])
        # col = 'v'  以及  col IN ('a','b')
        for m in re.finditer(rf"\b{col}\b\s*=\s*'([^']*)'", sql, flags=re.I):
            lit = m.group(1)
            # 排除被 LIKE 包住的情况
            if re.search(rf"\b{col}\b\s+like\b", sql[:m.end()], flags=re.I):
                continue
            if lit not in valid:
                issues.append({"column": col, "literal": lit,
                               "suggest": _closest(lit, valid), "op": "="})
        for m in re.finditer(rf"\b{col}\b\s+in\s*\(([^)]*)\)", sql, flags=re.I):
            for lit in re.findall(r"'([^']*)'", m.group(1)):
                if lit not in valid:
                    issues.append({"column": col, "literal": lit,
                                   "suggest": _closest(lit, valid), "op": "IN"})
    return issues


def _closest(lit: str, valid: set[str]) -> str | None:
    import difflib
    hit = difflib.get_close_matches(lit, list(valid), n=1, cutoff=0.3)
    return hit[0] if hit else None


def build_messages(question: str, schema_text: str, dim_text: str = "") -> list[dict]:
    ex = "\n".join(f"问：{e['q']}\nSQL：{e['sql']}\n" for e in FEW_SHOT)
    system = SYSTEM_PROMPT.format(
        examples="参考示例：\n" + ex,
        dims=dim_text or "（本环境未配置维度取值表，等值匹配请用 LIKE 兜底）",
        schema=schema_text,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"问题：{question}"},
    ]


def clean_sql(text: str) -> str:
    s = text.strip()
    s = re.sub(r"^```(?:sql)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    # 模型有时会带 "SQL：" 前缀
    s = re.sub(r"^(?:SQL|sql)\s*[:：]\s*", "", s)
    return s.strip()


# ---------------------------------------------------------------- 规则兜底
def offline_sql(question: str) -> str:
    """无 API Key 时的兜底：按关键词拼模板。只覆盖高频问法，不是模型效果。"""
    q = question.lower()
    m = re.search(r"(?:前|top)\s*(\d+)", q)
    topn = int(m.group(1)) if m else 10
    # 「最近 7 天」这类窗口
    wm = re.search(r"(?:最近|近|过去)\s*(\d+)\s*天", question)
    win = int(wm.group(1)) if wm else None

    # 时间序列意图：每天 / 每日 / 趋势 / 走势（必须排在单指标之前，
    # 否则「最近7天每天的下单量」会被「订单量」关键词抢走）
    if any(k in question for k in ("每天", "每日", "趋势", "走势", "逐日")) or (
        win and "天" in question
    ):
        n = win or 7
        col = "order_cnt" if ("下单" in question or "订单" in question) else "gmv"
        return (
            f"SELECT stat_date, {col} FROM ydsh_dws.dws_sales_day "
            f"ORDER BY stat_date DESC LIMIT {n}"
        )

    # 上月 / 本月：按数据里最新月份回溯，避免「上月」落到空结果
    month_mod = None
    if "上月" in question or "上个月" in question:
        month_mod = "(SELECT MAX(year_month) FROM ydsh_dws.dws_sales_month)"
    elif "本月" in question or "这个月" in question:
        month_mod = "(SELECT MAX(year_month) FROM ydsh_dws.dws_sales_month)"

    # 品类
    if "品类" in q or "分类" in q:
        if "前" in q or "top" in q or "最高" in q:
            return (f"SELECT rank_no, category, gmv, gmv_share FROM ydsh_ads.ads_category_top10 "
                    f"WHERE rank_no <= {topn} ORDER BY rank_no")
        for cat in ["运动户外", "数码家电", "图书文娱", "服饰鞋包", "食品生鲜", "家居生活", "美妆个护", "母婴玩具"]:
            if cat in question:
                return (f"SELECT ROUND(SUM(gmv_alloc), 2) AS gmv FROM ydsh_dws.dws_sales_category_day "
                        f"WHERE category = '{cat}'")
        return "SELECT * FROM ydsh_ads.ads_category_top10 ORDER BY rank_no"

    # 省份
    prov = re.search(r"([\u4e00-\u9fa5]{2,4})(?:省|市)", question)
    if prov:
        name = prov.group(1)
        cond = f"WHERE province LIKE '%{name}%'"
        if month_mod:
            cond += f" AND substr(stat_date, 1, 7) = {month_mod}"
        return (f"SELECT ROUND(SUM(gmv), 2) AS gmv, SUM(paid_order_cnt) AS orders "
                f"FROM ydsh_dws.dws_sales_store_day {cond}")

    # 渠道
    if "渠道" in q:
        return ("SELECT channel, gmv, gmv_share, order_cnt, aov "
                "FROM ydsh_ads.ads_channel_contribution ORDER BY gmv DESC")

    # 门店
    if "门店" in q and ("前" in q or "top" in q):
        return "SELECT * FROM ydsh_ads.ads_store_top10 ORDER BY rank_no"

    # 用户分层
    if "用户" in q and ("分层" in q or "价值" in q or "rfm" in q):
        return ("SELECT segment, user_cnt, gmv, gmv_share FROM ydsh_ads.ads_user_value_segmentation "
                "ORDER BY gmv DESC")

    # 月份 / 环比
    mm = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", question)
    if mm:
        ym = f"{mm.group(1)}-{int(mm.group(2)):02d}"
        return (f"SELECT year_month, gmv, paid_order_cnt, mom_rate FROM ydsh_dws.dws_sales_month "
                f"WHERE year_month = '{ym}'")

    # 单指标
    for key, metric in [("客单价", "aov"), ("复购率", "repeat_rate"), ("取消率", "cancel_rate"),
                        ("退款率", "refund_rate"), ("活跃用户", "active_users"),
                        ("订单量", "order_cnt"), ("件单价", "unit_price")]:
        if key in question:
            return f"SELECT metric_name, metric_value, unit FROM ydsh_ads.ads_kpi_summary WHERE metric_key = '{metric}'"

    # 带时间修饰的销售额（上月 / 本月）
    if month_mod and any(k in question for k in ("销售", "gmv", "营收", "营业额")):
        return (f"SELECT year_month, gmv, paid_order_cnt, mom_rate FROM ydsh_dws.dws_sales_month "
                f"WHERE year_month = {month_mod}")

    # 默认总 GMV
    return ("SELECT metric_name, metric_value, unit FROM ydsh_ads.ads_kpi_summary "
            "WHERE metric_key = 'gmv'")


# ---------------------------------------------------------------- 主流程
def ask(question: str, offline: bool = False, explain: bool = False,
        verbose: bool = True) -> dict:
    schema_text = load_schema_text()
    use_llm = (not offline) and llm_client.available()
    trace = {"question": question, "mode": "llm" if use_llm else "offline",
             "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "attempts": []}

    if use_llm:
        dim_text = load_dim_text()
        messages = build_messages(question, schema_text, dim_text)
        sql, err = None, None
        dim_warn: list[dict] = []
        for attempt in range(3):
            try:
                if attempt == 0:
                    r = llm_client.chat(messages, temperature=0)
                else:
                    # 失败自愈：把报错回灌
                    messages.append({"role": "assistant", "content": sql})
                    messages.append({
                        "role": "user",
                        "content": f"上面这条 SQL 在 Hive 执行报错了：{err}\n请修正后重新只输出 SQL。",
                    })
                    r = llm_client.chat(messages, temperature=0)
                sql = clean_sql(r["content"])
                trace["attempts"].append({"sql": sql, "elapsed": r["elapsed"],
                                          "tokens": r["prompt_tokens"],
                                          "model": r.get("model")})
                validate_sql(sql, ALLOWED_TABLES)

                # 结果侧防线：等值匹配用了库里不存在的取值，执行虽成功但结果必为空
                dim_warn = check_dim_literals(sql)
                if dim_warn and attempt < 2:
                    detail = "；".join(
                        f"{w['column']} = '{w['literal']}' 不存在"
                        + (f"，应为 '{w['suggest']}'" if w["suggest"] else "")
                        for w in dim_warn
                    )
                    raise ValueError(
                        f"维度取值错误：{detail}。"
                        f"请改用维度取值表中列出的原始值，或改用 LIKE 模糊匹配。"
                    )

                rows = run_sql(sql)
                out = {"ok": True, "sql": sql, "rows": rows, "trace": trace}
                if dim_warn:
                    out["dim_warning"] = dim_warn
                    trace["dim_warning"] = dim_warn
                    if verbose:
                        print(f"  [取值告警] 仍有 {len(dim_warn)} 处维度取值可疑，"
                              f"结果可能为空：{dim_warn}")
                return out
            except (SqlRejected, RuntimeError, Exception) as e:  # noqa: BLE001
                err = str(e)[:300]
                trace["attempts"].append({"sql": sql, "error": err})
                if verbose:
                    print(f"  [第 {attempt + 1} 次失败] {err[:120]}")
        return {"ok": False, "sql": sql, "rows": [], "error": err, "trace": trace}

    # 离线兜底
    sql = offline_sql(question)
    validate_sql(sql, ALLOWED_TABLES)
    rows = run_sql(sql)
    trace["attempts"].append({"sql": sql, "mode": "rule-based"})
    return {"ok": True, "sql": sql, "rows": rows, "trace": trace}


def print_result(res: dict, explain: bool = False):
    if not res["ok"]:
        print(f"\n[失败] {res.get('error')}")
        return
    print(f"\nSQL：{res['sql']}")
    rows = res["rows"]
    if not rows:
        print("结果：空")
        return
    cols = list(rows[0].keys())
    print("结果：")
    widths = {c: max(len(str(c)), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  " + " | ".join(c.ljust(widths[c]) for c in cols))
    print("  " + "-+-".join("-" * widths[c] for c in cols))
    for r in rows[:20]:
        print("  " + " | ".join(str(r[c]).ljust(widths[c]) for c in cols))
    if len(rows) > 20:
        print(f"  ... 共 {len(rows)} 行")

    if explain and llm_client.available() and res["trace"]["mode"] == "llm":
        try:
            payload = json.dumps(rows[:10], ensure_ascii=False)
            r = llm_client.chat([
                {"role": "system", "content": "你是数据分析师，用两三句中文解读查询结果，不要复述数字表格。"},
                {"role": "user", "content": f"问题：{res['trace']['question']}\n查询结果：{payload}"},
            ])
            print(f"\n解读：{r['content'].strip()}")
        except Exception as e:  # noqa: BLE001
            print(f"\n（解读失败：{e}）")


def log_trace(trace: dict):
    LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(trace, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description="NL2SQL 自然语言查数")
    ap.add_argument("-q", "--question", help="自然语言问题")
    ap.add_argument("--offline", action="store_true", help="强制走规则兜底（不用大模型）")
    ap.add_argument("--explain", action="store_true", help="额外让模型解读结果")
    args = ap.parse_args()

    if not args.question:
        ap.print_help()
        return 1

    mode = "大模型" if (llm_client.available() and not args.offline) else "规则兜底"
    print(f"问题：{args.question}")
    print(f"模式：{mode}")
    res = ask(args.question, offline=args.offline, explain=args.explain)
    print_result(res, explain=args.explain)
    log_trace(res["trace"])
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
