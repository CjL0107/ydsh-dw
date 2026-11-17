#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 4 周 · 子任务 5：查询优化对比 + 并发压测
================================================
用法：
    python apps/perf/bench.py --sql     # 4 组查询优化对比（分区裁剪 / 列裁剪 / 倾斜 / 三层选型）
    python apps/perf/bench.py --api     # JSON 接口并发压测（5 档并发）
    python apps/perf/bench.py           # 两个都跑

结果写到 docs/运行结果/第4周_压测结果.json，同时在终端打两张 markdown 表，
方便直接贴进《压力测试记录》。

三条统计口径（写进报告，避免数字被误读）：
1) SQL 侧每档跑 1 次预热 + 3 次正式，取**中位数**。首次查询要编译 Tez DAG、
   拉容器，单看第一次会把耗时高估 3~5 倍。
2) 接口侧按「固定请求数 / 并发数」跑，不用固定时长 —— 固定时长会让慢的档位
   样本偏少，P95 失真。
3) 失败请求单独计数，不计入延迟分位数。混进去算平均会把「超时 30 秒」
   和「成功 30 毫秒」摊成一个漂亮的假数字。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

OUT = ROOT / "docs" / "运行结果" / "第4周_压测结果.json"


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

# ---------------------------------------------------------------
# 一、查询优化对比
# ---------------------------------------------------------------
CASE_SQL = {
    # 分区裁剪：谓词直接写在分区列上，Metastore 能算出只扫一个目录
    "A1": "select count(1) as c from ydsh_dwd.dwd_order where order_date = '{d}'",
    # 同一个过滤条件，但把分区列裹进函数 —— Hive 无法下推，退化成全表扫描
    "A2": "select count(1) as c from ydsh_dwd.dwd_order where substr(order_date, 1, 10) = '{d}'",

    # 列裁剪：ORC 按列存，只读 pay_amount 一列 vs 读全部 17 列
    "B1": "select sum(pay_amount) as s from ydsh_dwd.dwd_order where order_date = '{d}'",
    "B2": "select count(1) as c from (select * from ydsh_dwd.dwd_order where order_date = '{d}') t",

    # 数据倾斜：直接 group by store_id，少数大店的分区数据量是小区的好几倍
    "C1": ("select store_id, sum(pay_amount) as gmv from ydsh_dwd.dwd_order "
           "where order_date = '{d}' group by store_id"),
    # 加盐两阶段：先按 (store_id + 随机盐) 局部聚合，再按 store_id 汇总
    "C2": ("select store_id, sum(part) as gmv from ("
           "  select store_id, cast(rand() * 4 as int) as salt, sum(pay_amount) as part "
           "  from ydsh_dwd.dwd_order where order_date = '{d}' "
           "  group by store_id, cast(rand() * 4 as int)"
           ") t group by store_id"),

    # 三层选型：同一个业务问题「各省 GMV 排名」
    "D1": ("select s.province, round(sum(o.pay_amount), 2) as gmv "
           "from ydsh_dwd.dwd_order o join ydsh_dwd.dim_store s on o.store_id = s.store_id "
           "where o.order_date = '{d}' group by s.province order by gmv desc"),
    "D2": "select province, gmv from ydsh_ads.ads_province_top10 order by gmv desc",
    "D3": "select province, gmv from ads_province_top10 order by gmv desc",          # MySQL
    "D4": "select province, gmv from ads_province_top10 order by gmv desc",          # ClickHouse
}

CASE_META = {
    "A1": ("分区裁剪 · 开启", "hive"),
    "A2": ("分区裁剪 · 关闭（函数包裹分区列）", "hive"),
    "B1": ("列裁剪 · 只读 1 列（pay_amount）", "hive"),
    "B2": ("列裁剪 · 读全部 17 列", "hive"),
    "C1": ("分组聚合 · 直接 group by（有倾斜）", "hive"),
    "C2": ("分组聚合 · 加盐两阶段", "hive"),
    "D1": ("省 GMV · Hive 明细现算（join 维表）", "hive"),
    "D2": ("省 GMV · Hive ADS 预聚合", "hive"),
    "D3": ("省 GMV · MySQL ADS（仪表板数据源）", "mysql"),
    "D4": ("省 GMV · ClickHouse ADS", "clickhouse"),
}

GROUPS = [
    ("A", "分区裁剪：谓词能否下推到分区列"),
    ("B", "列裁剪：列存读 1 列 vs 读全部列"),
    ("C", "数据倾斜：直接 group by vs 加盐两阶段"),
    ("D", "三层选型：同一业务问题的四条取数路径"),
]


def _mysql_rows(sql: str, db: str = "ydsh_ads"):
    import pymysql
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3307")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_ROOT_PASSWORD", "root"),
        database=db, charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _ch_rows(sql: str):
    import requests
    r = requests.post(
        f"http://{os.getenv('CLICKHOUSE_HOST', '127.0.0.1')}:{os.getenv('CLICKHOUSE_PORT', '8123')}/",
        params={"database": os.getenv("CLICKHOUSE_DB", "ydsh_ads"), "default_format": "JSON"},
        data=(sql + " FORMAT JSON").encode(),
        auth=(os.getenv("CLICKHOUSE_USER", "default"), os.getenv("CLICKHOUSE_PASSWORD", "")),
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["data"]


def _hive_rows(sql: str):
    from hive_runner import run_sql
    return run_sql(sql, max_rows=400)


RUNNERS = {"hive": _hive_rows, "mysql": _mysql_rows, "clickhouse": _ch_rows}


def _pick_partition() -> str:
    """挑一个数据量最大的分区做基准日 —— 空分区测不出差异。"""
    from hive_runner import run_sql
    rows = run_sql(
        "select order_date, count(1) as c from ydsh_dwd.dwd_order "
        "group by order_date order by c desc limit 1", max_rows=5)
    return rows[0]["order_date"]


def bench_sql(rounds: int = 3) -> dict:
    day = _pick_partition()
    print(f"[sql] 基准分区 order_date = {day}，每档 1 次预热 + {rounds} 次计时\n")
    out = {"partition": day, "cases": []}

    for key, (label, engine) in CASE_META.items():
        sql = CASE_SQL[key].format(d=day)
        fn = RUNNERS[engine]
        times, rows, err = [], None, None
        try:
            fn(sql)                                    # 预热：编译 DAG / 建连接
            for _ in range(rounds):
                t0 = time.perf_counter()
                rows = fn(sql)
                times.append((time.perf_counter() - t0) * 1000)
            ms = round(statistics.median(times), 1)
            print(f"  {key}  {label:<34} {ms:>9.1f} ms   ({len(rows)} 行)")
        except Exception as e:                          # noqa: BLE001
            ms, err = None, str(e)[:300]
            print(f"  {key}  {label:<34}   失败: {err.splitlines()[0][:80]}")
        out["cases"].append({
            "key": key, "label": label, "engine": engine,
            "ms": ms, "rows": None if rows is None else len(rows),
            "samples_ms": [round(t, 1) for t in times], "error": err,
        })

    # 分组小结：把「快了多少倍」直接算出来，报告里不用手算
    by_key = {c["key"]: c for c in out["cases"]}
    out["findings"] = []
    for base, fast, note in [
        ("A2", "A1", "分区裁剪"),
        ("B2", "B1", "列裁剪"),
        ("C1", "C2", "倾斜治理"),
        ("D1", "D3", "明细现算 → MySQL ADS"),
    ]:
        b, f = by_key.get(base), by_key.get(fast)
        if b and f and b["ms"] and f["ms"]:
            out["findings"].append({
                "topic": note,
                "slow": f'{b["label"]} {b["ms"]}ms',
                "fast": f'{f["label"]} {f["ms"]}ms',
                "ratio": round(b["ms"] / f["ms"], 2),
            })
    return out


# ---------------------------------------------------------------
# 二、接口并发压测
# ---------------------------------------------------------------
LEVELS = [1, 5, 20, 50]
REQ_PER_LEVEL = 200
ENDPOINTS = ["/api/overview", "/api/trend", "/api/province", "/api/category"]


def _one(url: str) -> tuple[float, bool]:
    import requests
    t0 = time.perf_counter()
    try:
        r = requests.get(url, timeout=10)
        ok = r.status_code == 200 and len(r.content) > 32
    except Exception:                                   # noqa: BLE001
        ok = False
    return (time.perf_counter() - t0) * 1000, ok


def bench_api(port: int = 8000) -> dict:
    base = f"http://127.0.0.1:{port}"
    print(f"\n[api] {base}  每档 {REQ_PER_LEVEL} 请求，端点轮转 {ENDPOINTS}\n")
    out = {"base": base, "levels": []}

    for conc in LEVELS:
        urls = [base + ENDPOINTS[i % len(ENDPOINTS)] for i in range(REQ_PER_LEVEL)]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=conc) as pool:
            results = list(pool.map(_one, urls))
        wall = time.perf_counter() - t0

        lat = sorted(ms for ms, ok in results if ok)
        fails = sum(1 for _, ok in results if not ok)
        if lat:
            p = lambda q: round(lat[min(int(len(lat) * q), len(lat) - 1)], 1)   # noqa: E731
            row = {
                "concurrency": conc, "requests": REQ_PER_LEVEL,
                "ok": len(lat), "failed": fails,
                "qps": round(REQ_PER_LEVEL / wall, 1),
                "p50": p(0.5), "p95": p(0.95), "max": round(lat[-1], 1),
            }
        else:
            row = {"concurrency": conc, "requests": REQ_PER_LEVEL, "ok": 0,
                   "failed": fails, "qps": 0, "p50": None, "p95": None, "max": None}
        print(f"  并发 {conc:>2}  成功 {row['ok']:>3}/{REQ_PER_LEVEL}  "
              f"QPS {row['qps']:>7}  P50 {row['p50']}ms  P95 {row['p95']}ms  最大 {row['max']}ms")
        out["levels"].append(row)
    return out


def bench_explain() -> dict:
    """量化「裁剪到底省了多少」，补墙钟时间测不出来的部分。

    为什么非要有这一步：
        1.5 万行的单机 Hive，任何一条简单聚合都要 4.1 秒 —— 这 4.1 秒基本全是
        Tez session 启动 + 容器拉起，真正扫描只占几十毫秒。于是「开/关分区裁剪」
        的墙钟时间只差 1~3%，光看时间会得出「优化没用」的错误结论。

    为什么不用 EXPLAIN：
        本来打算解析 `EXPLAIN` 里的 `Statistics: Num rows`，实测 Hive 4 的 Tez
        计划对这两种写法给出完全相同的行数估计（都是 TableScan rows=60），
        因为表上没收集列统计信息，CBO 只能瞎猜；计划里也不打印实际裁剪到的分区。
        所以改用**结构证据**：分区总数 vs 单分区行数 —— 这才是裁剪真正省下的量。
    """
    from hive_runner import run_sql
    day = _pick_partition()
    print("\n[explain] 量化分区裁剪（结构证据，比墙钟时间可靠）\n")
    out = {"partition": day}
    try:
        parts = run_sql("show partitions ydsh_dwd.dwd_order", max_rows=500, sep="tsv")
        n_parts = len(parts)
        total = int(run_sql("select count(1) as c from ydsh_dwd.dwd_order")[0]["c"])
        one = int(run_sql(f"select count(1) as c from ydsh_dwd.dwd_order "
                          f"where order_date = '{day}'")[0]["c"])
        out.update({
            "partitions": n_parts,
            "rows_total": total,
            "rows_one_partition": one,
            # 裁剪后只需读 1 个分区，理论上省掉 (n-1)/n 的 IO
            "io_saving_pct": round((n_parts - 1) / n_parts * 100, 1) if n_parts else None,
            "rows_ratio": round(total / one, 1) if one else None,
        })
        print(f"  分区总数={n_parts}  全表行数={total}  单分区行数={one}")
        print(f"  分区裁剪后只需读 1/{n_parts} 个目录，理论 IO 节省 "
              f"{out['io_saving_pct']}%")
    except Exception as e:                              # noqa: BLE001
        out["error"] = str(e)[:200]
        print(f"  失败: {str(e)[:100]}")
    return out


# ---------------------------------------------------------------
# 三、输出
# ---------------------------------------------------------------
def _md_sql(res: dict) -> str:
    lines = ["| 用例 | 查询路径 | 引擎 | 中位耗时(ms) | 返回行数 |", "|---|---|---|---:|---:|"]
    for g, title in GROUPS:
        lines.append(f"| **{g}** | *{title}* | | | |")
        for c in res["cases"]:
            if not c["key"].startswith(g):
                continue
            ms = "失败" if c["ms"] is None else f'{c["ms"]:.1f}'
            lines.append(f'| {c["key"]} | {c["label"]} | {c["engine"]} | {ms} | {c["rows"] or "-"} |')
    if res.get("findings"):
        lines += ["", "**倍数关系**", ""]
        for f in res["findings"]:
            lines.append(f'- {f["topic"]}：{f["slow"]} → {f["fast"]}，**{f["ratio"]}×**')
    return "\n".join(lines)


def _md_api(res: dict) -> str:
    lines = ["| 并发数 | 请求数 | 成功 | 失败 | QPS | P50(ms) | P95(ms) | 最大(ms) |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in res["levels"]:
        lines.append(f'| {r["concurrency"]} | {r["requests"]} | {r["ok"]} | {r["failed"]} | '
                     f'{r["qps"]} | {r["p50"]} | {r["p95"]} | {r["max"]} |')
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql", action="store_true", help="只跑查询优化对比")
    ap.add_argument("--api", action="store_true", help="只跑接口压测")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    do_sql = a.sql or not (a.sql or a.api)
    do_api = a.api or not (a.sql or a.api)

    result = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    if do_sql:
        result["sql"] = bench_sql(a.rounds)
        result["explain"] = bench_explain()
    if do_api:
        result["api"] = bench_api(a.port)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    if do_sql:
        print("\n查询优化对比\n")
        print(_md_sql(result["sql"]))
    if do_api:
        print("\n接口并发压测\n")
        print(_md_api(result["api"]))
    print(f"\n结果已写入 {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
