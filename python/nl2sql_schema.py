#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 3 周 · 子任务 4（上）：Hive Schema 自动抽取
================================================
作用：把 Hive 里的库表结构（表名、字段名、类型、字段注释）抽成 JSON，
      再拼成一段紧凑的「数据库元信息」注入 Prompt，让大模型知道有哪些表可查。

为什么要用真实抽取而不是手写：
  模型最容易犯的错就是"编表名/编字段名"。手写 schema 容易和库里漂移，
  一旦 DDL 改了而 Prompt 没改，NL2SQL 的正确率会无声地下滑。
  这里每次运行都从 Hive 现取，schema.json 带生成时间戳。

用法：
  python python/nl2sql_schema.py            # 抽取并写 python/outputs/schema.json
  python python/nl2sql_schema.py --show     # 抽取后打印 Prompt 片段
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from hive_runner import describe, run_sql, show_tables

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "python" / "outputs" / "schema.json"
DIM_OUT = ROOT / "python" / "outputs" / "dim_values.json"

# 需要告诉模型"真实取值长什么样"的维度列。
# 起因：实测模型把「广东省」写成 WHERE province = '广东'，等值匹配查出 NULL。
# 只看字段名和注释，模型无从知道库里存的是"广东省"还是"广东"，
# 所以必须把实际取值一并注入 Prompt。
DIM_SPECS = [
    ("province", "ydsh_dws.dws_sales_store_day", "province", "省份（存的是全称，如「广东省」，不是「广东」）"),
    ("city", "ydsh_dws.dws_sales_store_day", "city", "城市"),
    ("store_level", "ydsh_dws.dws_sales_store_day", "store_level", "门店分级"),
    ("channel", "ydsh_dws.dws_sales_channel_day", "channel", "销售渠道"),
    ("category", "ydsh_dws.dws_sales_category_day", "category", "商品品类"),
    ("year_month", "ydsh_dws.dws_sales_month", "year_month", "月份，格式 yyyy-MM"),
    # KPI 表是竖表，指标名也是"维度值"。实测模型写 metric_name = '客单价'
    # 查出 0 行——库里存的是「客单价 AOV」。这跟「广东 / 广东省」是同一类错误。
    ("metric_key", "ydsh_ads.ads_kpi_summary", "metric_key", "KPI 指标键（英文小写）"),
    ("metric_name", "ydsh_ads.ads_kpi_summary", "metric_name",
     "KPI 指标中文名（带单位后缀，如「客单价 AOV」，不是「客单价」）"),
]

# 只暴露业务可查的库，ODS 层是原始脏数据，不开放给 NL2SQL
DATABASES = ["ydsh_dws", "ydsh_ads"]

# 表级业务说明（字段注释已在 DDL 里，这里补"这张表能回答什么问题"）
TABLE_NOTES = {
    "ydsh_dws.dws_sales_day": "销售日汇总（全站）。按天一行，含 GMV、订单量、客单价、取消率。问「某天/某月/某段时间的销售额」优先用这张表",
    "ydsh_dws.dws_sales_category_day": "品类×天汇总。gmv_alloc 是按明细占比分摊后的 GMV。问「某品类销售额」用这张",
    "ydsh_dws.dws_sales_store_day": "门店×天汇总，含省份城市与门店分级。问「某省/某市/某门店销售额」用这张",
    "ydsh_dws.dws_sales_channel_day": "渠道×天汇总（APP/小程序/PC官网/门店）",
    "ydsh_dws.dws_sales_week": "销售周汇总，含周一日期",
    "ydsh_dws.dws_sales_month": "销售月汇总，含环比 mom_rate",
    "ydsh_dws.dws_user_order_agg": "用户级汇总：首单/末单日期、订单数、累计消费、省份。问「用户消费排名/复购」用这张",
    "ydsh_ads.ads_kpi_summary": "核心 KPI 竖表（一行一个指标），含口径说明 definition。问「整体 GMV/客单价/复购率是多少」用这张",
    "ydsh_ads.ads_sales_daily_report": "销售日报，含日环比 gmv_dod、7 日移动平均、上周同日对比",
    "ydsh_ads.ads_category_top10": "品类销售额 TopN，含占比 gmv_share",
    "ydsh_ads.ads_user_value_segmentation": "用户 RFM 八分层结果，含人数/GMV/人均消费",
    "ydsh_ads.ads_channel_contribution": "渠道贡献，含 GMV 占比与客单价",
    "ydsh_ads.ads_province_top10": "省份销售额 TopN",
    "ydsh_ads.ads_store_top10": "门店销售额 TopN",
}


def extract() -> dict:
    schema = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "databases": {}}
    for db in DATABASES:
        tables = show_tables(db)
        schema["databases"][db] = {}
        for t in tables:
            cols = describe(db, t)
            fq = f"{db}.{t}"
            schema["databases"][db][t] = {
                "note": TABLE_NOTES.get(fq, ""),
                "columns": [
                    {"name": c, "type": ty, "comment": cm} for c, ty, cm in cols
                ],
            }
            print(f"  抽取 {fq}: {len(cols)} 个字段")
    return schema


def extract_dims(sample_limit: int = 60) -> dict:
    """抽取维度列的实际取值，供 Prompt 注入与结果侧校验共用。

    取值超过 sample_limit 时只保留前若干个并标记 truncated——
    Prompt 塞太长会挤压 Schema 的注意力，反而降准确率。
    """
    dims = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "dims": {}}
    for name, table, col, note in DIM_SPECS:
        try:
            rows = run_sql(
                f"SELECT {col} AS v, COUNT(1) AS c FROM {table} "
                f"GROUP BY {col} ORDER BY c DESC"
            )
        except Exception as e:  # noqa: BLE001
            print(f"  跳过 {name}：{e}")
            continue
        vals = [str(r["v"]) for r in rows if r.get("v") not in (None, "", "NULL")]
        dims["dims"][name] = {
            "table": table,
            "column": col,
            "note": note,
            "values": vals[:sample_limit],
            "truncated": len(vals) > sample_limit,
            "total": len(vals),
        }
        print(f"  维度 {name}({table}.{col}): {len(vals)} 个取值")
    return dims


def to_dim_text(dims: dict) -> str:
    """把维度取值压成给大模型看的文本。等值匹配必须用这里的原值。"""
    lines = ["维度取值（WHERE 做等值匹配时**必须逐字使用下列原始值**，不要简化或改写）："]
    for name, d in dims["dims"].items():
        vals = "、".join(f"'{v}'" for v in d["values"])
        tail = f" 等 {d['total']} 个" if d["truncated"] else ""
        lines.append(f"- {d['table']}.{d['column']}（{d['note']}）：{vals}{tail}")
    return "\n".join(lines)


def to_prompt_text(schema: dict, max_cols: int = 40) -> str:
    """把 schema 压成给大模型看的紧凑文本（只保留中文注释，丢掉 long 类型噪声）"""
    lines = []
    for db, tables in schema["databases"].items():
        for t, info in tables.items():
            fq = f"{db}.{t}"
            lines.append(f"\n表 {fq}  —— {info['note']}")
            cols = info["columns"][:max_cols]
            parts = []
            for c in cols:
                cm = c["comment"] or ""
                parts.append(f"{c['name']}({c['type']}){'：' + cm if cm else ''}")
            lines.append("  字段：" + "；".join(parts))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="打印 Prompt 片段")
    args = ap.parse_args()

    print("从 Hive 抽取 Schema ...")
    schema = extract()
    OUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    n_tables = sum(len(v) for v in schema["databases"].values())
    print(f"\n已写入 {OUT}（{n_tables} 张表）")

    print("\n抽取维度取值 ...")
    dims = extract_dims()
    DIM_OUT.write_text(json.dumps(dims, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {DIM_OUT}（{len(dims['dims'])} 个维度）")

    if args.show:
        print("\n" + "=" * 60)
        print(to_dim_text(dims))
        print("\n" + "=" * 60)
        print(to_prompt_text(schema))
    return 0


if __name__ == "__main__":
    sys.exit(main())
