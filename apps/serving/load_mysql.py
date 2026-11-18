#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 4 周 · MySQL 集市同步（Superset 图表数据源）
================================================
把 Hive 的 ADS 结果表 + 两张可视化派生表同步到 MySQL 的 ydsh_ads 库。

为什么不直接让 Superset 连 Hive：
    任务书要求"Superset 连接 Hive 与 MySQL"两者，但两者的定位不同：
      · Hive  —— 即席探索，数据全、但一次查询 5~9 秒，点筛选器等不起
      · MySQL —— 放 421 行 ADS 结果，图表秒出，适合做仪表板
    这也是企业里常见做法：**重计算留在 Hive，轻展示放 MySQL**。

为什么不放 ClickHouse：
    试过 clickhouse+http 方言，Superset 4.1 的 ClickHouseEngineSpec 加载失败
    （sqlalchemy.dialects:clickhouse.http 找不到）。不为了一个驱动去改镜像，
    ClickHouse 仍然保留在编排里，用于"大结果集切片查询"的压测对比。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pymysql

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from hive_runner import describe, run_sql  # noqa: E402


def _load_dotenv() -> None:
    """口令从仓库根 .env 读，代码里不留明文。"""
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

# 宿主 3307 -> 容器 3306（本机 MySQL80 占了 3306）
MYSQL = dict(
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT", "3307")),
    user=os.getenv("MYSQL_USER", "root"),
    password=os.getenv("MYSQL_ROOT_PASSWORD", "root"),
    charset="utf8mb4",
)
DB = os.getenv("MYSQL_ADS_DB", "ydsh_ads")

MIRROR = [
    ("ads_kpi_summary", "ydsh_ads.ads_kpi_summary", "metric_key"),
    ("ads_sales_daily_report", "ydsh_ads.ads_sales_daily_report", "stat_date"),
    ("ads_category_top10", "ydsh_ads.ads_category_top10", "category"),
    ("ads_province_top10", "ydsh_ads.ads_province_top10", "province"),
    ("ads_channel_contribution", "ydsh_ads.ads_channel_contribution", "channel_code"),
    ("ads_store_top10", "ydsh_ads.ads_store_top10", "store_id"),
    ("ads_user_value_segmentation", "ydsh_ads.ads_user_value_segmentation", "segment"),
]

PAID = "('OS02','OS03','OS05')"

# 可视化宽表：2.6 万行，不能走默认的 5000 行上限（会被静默截断成前 5000 行，
# 表现为图表总数偏小、且很难一眼发现）。单独列出来，把上限提到 5 万。
WIDE = [
    ("ads_viz_wide", "ydsh_ads.ads_viz_wide", "order_id", 50000),
]


DERIVED = [
    ("ads_payment_share",
     f"select pay_type, count(1) orders, round(sum(pay_amount), 2) gmv, round(avg(pay_amount), 2) aov "
     f"from ydsh_dwd.dwd_order where status_code in {PAID} group by pay_type",
     [("pay_type", "VARCHAR(32)"), ("orders", "INT"), ("gmv", "DECIMAL(16,2)"), ("aov", "DECIMAL(14,2)")],
     "pay_type"),
    ("ads_hour_category",
     f"select o.order_hour AS hour, p.category, round(sum(i.amount), 2) gmv, count(1) cnt "
     f"from ydsh_dwd.dwd_order o join ydsh_dwd.dwd_order_item i on o.order_id = i.order_id "
     f"join ydsh_dwd.dim_product p on i.product_id = p.product_id "
     f"where o.status_code in {PAID} group by o.order_hour, p.category",
     [("hour", "INT"), ("category", "VARCHAR(32)"), ("gmv", "DECIMAL(16,2)"), ("cnt", "INT")],
     "hour"),
]


def mysql_type(hive_type: str) -> str:
    t = hive_type.strip().lower()
    if t.startswith("decimal"):
        return "DECIMAL(18,4)"          # 统一放大，避免精度截断
    if t in ("int", "bigint", "smallint"):
        return "BIGINT"
    if t in ("double", "float"):
        return "DOUBLE"
    return "VARCHAR(255)"


def main() -> None:
    conn = pymysql.connect(**MYSQL, autocommit=True)
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS {DB} CHARACTER SET utf8mb4")
    cur.execute(f"USE {DB}")   # 不 USE 的话后面每条 DDL 都会报 1046
    print(f"[mysql] 库 {DB} 就绪")

    for table, source, _order in MIRROR:
        db, tb = source.split(".")
        cols = [(c, t) for c, t, _ in describe(db, tb)]
        rows = run_sql(f"select * from {source}", max_rows=5000)
        _load(cur, table, cols, rows, source)

    for table, sql, cols, _order in DERIVED:
        rows = run_sql(sql, max_rows=500)
        _load(cur, table, cols, rows, "derived")

    for table, source, _order, limit in WIDE:
        db, tb = source.split(".")
        cols = [(c, t) for c, t, _ in describe(db, tb)]
        rows = run_sql(f"select * from {source}", max_rows=limit)
        if len(rows) >= limit:
            raise RuntimeError(f"{table} 取到 {len(rows)} 行，已撞上限，会被截断")
        _load(cur, table, cols, rows, source)

    cur.close()
    conn.close()
    print("[mysql] 同步完成")


def _clean(v):
    """Hive 的空值到客户端可能变成 ''、'NULL'、'NaN'、'\\N' 四种形态。

    beeline 输出 CSV 时是字符串 'NULL'，pyhive 走 Thrift 时是 None 或 ''。
    不归一化就会在 decimal 列上报
        (1366, "Incorrect decimal value: '' for column 'gmv_dod'")
    —— 这个坑在第 3 周 ClickHouse 装载时也踩过一次。
    """
    if v is None:
        return None
    if isinstance(v, str) and v.strip().lower() in ("", "null", "nan", "\\n", "none"):
        return None
    return v


def _load(cur, table: str, cols, rows, source: str) -> None:
    ddl = ",\n  ".join(f"`{c}` {mysql_type(t)}" for c, t in cols)
    cur.execute(f"DROP TABLE IF EXISTS `{table}`")
    cur.execute(f"CREATE TABLE `{table}` (\n  {ddl}\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
    if rows:
        names = [c for c, _ in cols]
        placeholders = ",".join(["%s"] * len(names))
        sql = f"INSERT INTO `{table}` ({','.join('`%s`' % n for n in names)}) VALUES ({placeholders})"
        cur.executemany(sql, [[_clean(r.get(n)) for n in names] for r in rows])
    cur.execute(f"SELECT COUNT(*) FROM `{table}`")
    print(f"[mysql] {table:<30} 源={source:<38} 行数={cur.fetchone()[0]}")


if __name__ == "__main__":
    main()
