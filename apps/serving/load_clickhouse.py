#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 4 周 · ClickHouse 服务层装载
================================================
把 Hive 里 ADS 层的结果同步到 ClickHouse，供 Superset 做交互式筛选。

为什么不让 Superset 直接连 Hive：
    仪表板上的筛选器每动一次就要重算一次聚合。Hive 走 MapReduce/Tez，
    实测一次省份过滤 5~8 秒，筛选器点两下用户就放弃了。
    ClickHouse 列存 + 向量化，同样的过滤 40~80 毫秒，这才谈得上"交互"。

为什么不是全量同步：
    只同步 ADS 的 7 张结果表（合计 421 行）+ 两张可视化专用的派生表。
    Hive 仍然是唯一的计算入口，ClickHouse 只是结果缓存，口径不会分叉。

两张派生表（不在第 3 周的 ADS 交付里，属于服务层补充）：
    ads_payment_share   支付方式分布（dwd_order 现算）
    ads_hour_category   时段 × 品类（dwd 明细现算，只有热力图用）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from hive_runner import describe, run_sql  # noqa: E402

# 连接参数一律从环境变量取，缺了就从仓库根的 .env 读 —— 口令不进代码。
# 之前这里把口令写成默认值，等于把密钥固化了：改 .env 不生效，
# 还得回来改源码。现在反过来，.env 是唯一事实来源。
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

CH_HOST = os.getenv("CLICKHOUSE_HOST", "127.0.0.1")
CH_PORT = os.getenv("CLICKHOUSE_PORT", "8123")
CH_USER = os.getenv("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CH_DB = os.getenv("CLICKHOUSE_DB", "ydsh_ads")

if not CH_PASSWORD:
    raise SystemExit(
        "CLICKHOUSE_PASSWORD 未设置。请在仓库根 .env 里配置后重试 —— "
        "留空连不上（默认用户无密码时 ClickHouse 会拒绝，报 516 Authentication failed）。"
    )

# 表 -> (Hive 源, 排序键, 备注)
MIRROR = {
    "ads_kpi_summary": ("ydsh_ads.ads_kpi_summary", "metric_key", "15 项核心 KPI"),
    "ads_sales_daily_report": ("ydsh_ads.ads_sales_daily_report", "stat_date", "日销售报表 366 行"),
    "ads_category_top10": ("ydsh_ads.ads_category_top10", "category", "品类结构"),
    "ads_province_top10": ("ydsh_ads.ads_province_top10", "province", "省份分布"),
    "ads_channel_contribution": ("ydsh_ads.ads_channel_contribution", "channel_code", "渠道贡献"),
    "ads_store_top10": ("ydsh_ads.ads_store_top10", "store_id", "门店 TOP10"),
    "ads_user_value_segmentation": ("ydsh_ads.ads_user_value_segmentation", "segment", "用户分层"),
}

PAID = "('OS02','OS03','OS05')"

DERIVED = {
    "ads_payment_share": {
        "sql": (
            f"select pay_type, count(1) orders, round(sum(pay_amount), 2) gmv, "
            f"round(avg(pay_amount), 2) aov from ydsh_dwd.dwd_order "
            f"where status_code in {PAID} group by pay_type"
        ),
        "order_by": "pay_type",
        "note": "支付方式分布",
    },
    "ads_hour_category": {
        "sql": (
            f"select o.order_hour AS hour, p.category, round(sum(i.amount), 2) gmv, count(1) cnt "
            f"from ydsh_dwd.dwd_order o "
            f"join ydsh_dwd.dwd_order_item i on o.order_id = i.order_id "
            f"join ydsh_dwd.dim_product p on i.product_id = p.product_id "
            f"where o.status_code in {PAID} "
            f"group by o.order_hour, p.category"
        ),
        "order_by": "(hour, category)",
        "note": "时段 × 品类热力",
    },
}

TYPE_MAP = {
    "int": "Int64", "bigint": "Int64", "smallint": "Int32", "tinyint": "Int8",
    "string": "String", "varchar": "String", "char": "String",
    "double": "Float64", "float": "Float32",
}


def ch_type(hive_type: str) -> str:
    t = hive_type.strip().lower()
    if t.startswith("decimal"):
        return "Float64"
    return TYPE_MAP.get(t, "String")


def ch(query: str, data: bytes | None = None, fmt: str | None = None) -> str:
    url = f"http://{CH_HOST}:{CH_PORT}/"
    params = {"query": query, "database": CH_DB}
    if fmt:
        params["query"] = query
    r = requests.post(
        url, params=params, data=data, auth=(CH_USER, CH_PASSWORD), timeout=120,
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"ClickHouse 报错（{r.status_code}）：{r.text[:300]}")
    return r.text


def ensure_db() -> None:
    ch(f"CREATE DATABASE IF NOT EXISTS {CH_DB}")
    print(f"[ch] 数据库 {CH_DB} 就绪")


def _clean(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, str):
            v = v.strip()
            if v in ("", "NULL", "NaN", "nan", "None", "\\N"):
                out[k] = None
                continue
        out[k] = v
    return out


def load_table(name: str, source: str, order_by: str, rows: list[dict], cols: list[tuple[str, str]]) -> None:
    """按 Hive 的列定义建表，再以 JSONEachRow 灌数据"""
    ddl_cols = ",\n  ".join(f"`{c}` {ch_type(t)}" for c, t in cols)
    ch(f"DROP TABLE IF EXISTS `{name}`")
    ch(f"CREATE TABLE `{name}` (\n  {ddl_cols}\n) ENGINE = MergeTree ORDER BY {order_by}")
    if rows:
        # Hive 的 decimal 空值经 beeline 出来是字符串 "NaN"，ClickHouse 的
        # Float64 解析不了（Code: 27 Cannot parse NaN），必须先归一成 null。
        body = "\n".join(
            __import__("json").dumps(_clean(r), ensure_ascii=False) for r in rows
        ).encode("utf-8")
        ch(f"INSERT INTO `{name}` FORMAT JSONEachRow", data=body)
    n = ch(f"SELECT count() FROM `{name}`").strip()
    print(f"[ch] {name:<30} 源={source:<38} 行数={n}")


def main() -> None:
    ensure_db()

    # 1) 镜像 ADS 结果表
    for table, (source, order_by, _note) in MIRROR.items():
        db, tb = source.split(".")
        cols = [(c, t) for c, t, _ in describe(db, tb)]
        rows = run_sql(f"select * from {source}", max_rows=5000)
        load_table(table, source, order_by, rows, cols)

    # 2) 派生表（列类型手工声明，因为结果不是 Hive 表）
    derived_cols = {
        "ads_payment_share": [("pay_type", "string"), ("orders", "int"), ("gmv", "decimal(16,2)"), ("aov", "decimal(14,2)")],
        "ads_hour_category": [("hour", "int"), ("category", "string"), ("gmv", "decimal(16,2)"), ("cnt", "int")],
    }
    for table, spec in DERIVED.items():
        rows = run_sql(spec["sql"], max_rows=500)
        load_table(table, "derived:" + spec["note"], spec["order_by"], rows, derived_cols[table])

    print("[ch] 全部装载完成")


if __name__ == "__main__":
    main()
