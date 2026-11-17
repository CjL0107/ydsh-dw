#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 4 周 · 可视化数据服务层
================================================
职责：把 ADS / DWS 层的指标查询封装成结构化的 JSON 片段，
     供 ECharts 大屏、Streamlit 问答、Superset 之外的其它前端复用。

三条设计约束（都是被前几周踩过的坑逼出来的）：

1) **只读 ADS/DWS，不碰明细表**。大屏面板每 5 分钟刷新一次，
   若直接扫 dwd（3 万行明细 + 3 张维表 join），单次查询 7~8 秒，
   六个面板并发就把 HiveServer2 打满。ADS 层已经算好了 366 行日粒度结果。
   唯一例外是「时段 × 品类」热力图 —— ADS 层没有这个粒度，
   只能回明细算，因此单独标记 `slow=True`，放进快照时只算一次。

2) **数值必须转成 float/int 再出 API**。beeline 的返回值全是字符串，
   直接把 "2529.6400" 丢给前端，ECharts 会当成类目值画不出线。
   这是上周 schema 抽取事故的同一个教训：类型不对要当场转，别指望下游兜。

3) **每个取数函数返回 dict，不返回裸 list**。前端要知道单位、口径、
   时间范围，否则「客单价 2529」到底是不是含运费没人说得清。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from hive_runner import run_sql  # noqa: E402

# 有效订单口径：已支付 / 已完成 / 退款中（与第 3 周 GMV 口径保持一致）
PAID_STATUS = "('OS02','OS03','OS05')"


# ----------------------------------------------------------------------
# 类型转换
# ----------------------------------------------------------------------
def _f(v: Any) -> float | None:
    """字符串 → float，空值/NULL 返回 None（不是 0）。
    0 和「没有数据」在图表上含义完全不同，不能混。"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "NULL", "null", "None", "NaN"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _i(v: Any) -> int | None:
    f = _f(v)
    return None if f is None else int(round(f))


def _pct(v: Any) -> float | None:
    """ADS 层占比字段存的是小数（0.1491），前端要的是 14.91。
    第一版没转，大屏上品类占比直接显示成 "0.1491%"，看着像数据错了。"""
    f = _f(v)
    return None if f is None else round(f * 100, 2)


# ----------------------------------------------------------------------
# 单个取数函数
# ----------------------------------------------------------------------
def kpi() -> dict:
    """15 项核心 KPI，取全量后由前端挑展示项"""
    rows = run_sql(
        "select metric_key, metric_name, metric_value, unit, definition "
        "from ydsh_ads.ads_kpi_summary"
    )
    items = []
    for r in rows:
        items.append({
            "key": r["metric_key"],
            "name": r["metric_name"],
            "value": _f(r["metric_value"]),
            "unit": r["unit"],
            "definition": r["definition"],
        })
    return {"items": items, "period": "2024 全年", "source": "ydsh_ads.ads_kpi_summary"}


def trend() -> dict:
    """日粒度趋势：GMV、7 日均线、订单量、客单价"""
    # 366 行，超过 run_sql 默认 200 行上限 —— 第一天跑快照时这里只拿到 200 行，
    # 大屏趋势图直接少了 4 个半月。行数上限这种"安全阀"只在批处理里放宽，
    # 但必须显式写出来，不能指望调用方记得。
    rows = run_sql(
        "select stat_date, gmv, gmv_ma7, paid_order_cnt, aov, is_weekend "
        "from ydsh_ads.ads_sales_daily_report order by stat_date",
        max_rows=400,
    )
    # 结果侧防线：2024 全年应为 366 天（含闰日）。拿到的行数明显偏少时直接报错，
    # 否则大屏只是"趋势图短了一截"，没人会注意到数据被截断。
    if len(rows) < 360:
        raise RuntimeError(
            f"日序列只有 {len(rows)} 行，疑似被行数上限截断（应为 366 行）"
        )
    return {
        "dates": [r["stat_date"] for r in rows],
        "gmv": [_f(r["gmv"]) for r in rows],
        "ma7": [_f(r["gmv_ma7"]) for r in rows],
        "orders": [_i(r["paid_order_cnt"]) for r in rows],
        "aov": [_f(r["aov"]) for r in rows],
        "weekend": [_i(r["is_weekend"]) for r in rows],
        "source": "ydsh_ads.ads_sales_daily_report",
    }


def category() -> dict:
    """品类维度：GMV、占比、单量、件数、件单价"""
    rows = run_sql(
        "select rank_no, category, gmv, gmv_share, order_cnt, item_qty, avg_unit_price "
        "from ydsh_ads.ads_category_top10 order by rank_no"
    )
    return {
        "items": [{
            "rank": _i(r["rank_no"]),
            "name": r["category"],
            "gmv": _f(r["gmv"]),
            "share": _pct(r["gmv_share"]),
            "orders": _i(r["order_cnt"]),
            "qty": _i(r["item_qty"]),
            "avgPrice": _f(r["avg_unit_price"]),
        } for r in rows],
        "source": "ydsh_ads.ads_category_top10",
    }


def province() -> dict:
    """省份维度：地图着色用，名称与地图 GeoJSON 的 name 完全一致（带“省”后缀）"""
    rows = run_sql(
        "select rank_no, province, gmv, gmv_share, order_cnt, user_cnt, aov "
        "from ydsh_ads.ads_province_top10 order by rank_no"
    )
    return {
        "items": [{
            "rank": _i(r["rank_no"]),
            "name": r["province"],
            "gmv": _f(r["gmv"]),
            "share": _pct(r["gmv_share"]),
            "orders": _i(r["order_cnt"]),
            "users": _i(r["user_cnt"]),
            "aov": _f(r["aov"]),
        } for r in rows],
        "source": "ydsh_ads.ads_province_top10",
    }


def channel() -> dict:
    rows = run_sql(
        "select channel, channel_code, gmv, gmv_share, order_cnt, user_cnt, aov "
        "from ydsh_ads.ads_channel_contribution order by gmv desc"
    )
    return {
        "items": [{
            "name": r["channel"],
            "code": r["channel_code"],
            "gmv": _f(r["gmv"]),
            "share": _pct(r["gmv_share"]),
            "orders": _i(r["order_cnt"]),
            "users": _i(r["user_cnt"]),
            "aov": _f(r["aov"]),
        } for r in rows],
        "source": "ydsh_ads.ads_channel_contribution",
    }


def payment() -> dict:
    """支付方式占比。ADS 层没有这个口径，回 dwd_order 现算（4 种方式，很快）"""
    rows = run_sql(
        f"select pay_type, count(1) cnt, sum(pay_amount) amt, avg(pay_amount) aov "
        f"from ydsh_dwd.dwd_order where status_code in {PAID_STATUS} "
        f"group by pay_type order by amt desc"
    )
    return {
        "items": [{
            "name": r["pay_type"],
            "orders": _i(r["cnt"]),
            "gmv": round(_f(r["amt"]) or 0, 2),
            "aov": round(_f(r["aov"]) or 0, 2),
        } for r in rows],
        "source": "ydsh_dwd.dwd_order",
    }


def hour_category() -> dict:
    """时段 × 品类 热力图：24 小时 × 8 品类 = 192 个格子

    这是全项目唯一必须回明细的查询（ADS/DWS 都没有「小时」粒度），
    实测 7~8 秒，所以只在快照刷新时算一次，不放进实时接口。
    """
    rows = run_sql(
        f"""select o.order_hour h, p.category cat,
                   round(sum(i.amount), 2) amt, count(1) cnt
            from ydsh_dwd.dwd_order o
            join ydsh_dwd.dwd_order_item i on o.order_id = i.order_id
            join ydsh_dwd.dim_product p on i.product_id = p.product_id
            where o.status_code in {PAID_STATUS}
            group by o.order_hour, p.category"""
    )
    hours = sorted({_i(r["h"]) for r in rows})
    cats = sorted({r["cat"] for r in rows})
    cat_idx = {c: idx for idx, c in enumerate(cats)}
    data = [[_i(r["h"]), cat_idx[r["cat"]], round(_f(r["amt"]) or 0, 2)] for r in rows]
    return {
        "hours": [f"{h:02d}" for h in hours],
        "categories": cats,
        "data": data,
        "unit": "元",
        "slow": True,
        "source": "dwd_order × dwd_order_item × dim_product",
    }


def city_scatter() -> dict:
    """城市散点：客单价 vs 单量，气泡大小 = GMV

    散点图要回答的是「单量大的城市是不是客单价也高」，
    因此 x 用单量、y 用客单价，避免两个量纲混在一个轴上。
    """
    rows = run_sql(
        """select city, round(sum(gmv), 2) gmv, sum(paid_order_cnt) orders,
                  round(avg(aov), 2) aov
           from ydsh_dws.dws_sales_store_day
           group by city"""
    )
    items = [{
        "name": r["city"],
        "gmv": _f(r["gmv"]),
        "orders": _i(r["orders"]),
        "aov": _f(r["aov"]),
    } for r in rows if r.get("city")]
    items.sort(key=lambda x: x["gmv"] or 0, reverse=True)
    return {"items": items, "source": "ydsh_dws.dws_sales_store_day"}


def store_top() -> dict:
    rows = run_sql(
        "select rank_no, store_name, city, store_level, gmv, gmv_share, order_cnt, aov "
        "from ydsh_ads.ads_store_top10 order by rank_no"
    )
    return {
        "items": [{
            "rank": _i(r["rank_no"]),
            "name": r["store_name"],
            "city": r["city"],
            "level": r["store_level"],
            "gmv": _f(r["gmv"]),
            "share": _pct(r["gmv_share"]),
            "orders": _i(r["order_cnt"]),
            "aov": _f(r["aov"]),
        } for r in rows],
        "source": "ydsh_ads.ads_store_top10",
    }


def user_segment() -> dict:
    rows = run_sql(
        "select segment, user_cnt, user_share, gmv, gmv_share, avg_gmv, avg_freq "
        "from ydsh_ads.ads_user_value_segmentation"
    )
    return {
        "items": [{
            "name": r["segment"],
            "users": _i(r["user_cnt"]),
            "userShare": _pct(r["user_share"]),
            "gmv": _f(r["gmv"]),
            "gmvShare": _pct(r["gmv_share"]),
            "avgGmv": _f(r["avg_gmv"]),
            "avgFreq": _f(r["avg_freq"]),
        } for r in rows],
        "source": "ydsh_ads.ads_user_value_segmentation",
    }


# 函数名 -> (标题, 是否慢查询)
PANELS: dict[str, tuple[str, bool]] = {
    "kpi": ("核心 KPI", False),
    "trend": ("日销售趋势", False),
    "category": ("品类结构", False),
    "province": ("省份分布", False),
    "channel": ("渠道贡献", False),
    "payment": ("支付方式", False),
    "city_scatter": ("城市客单价 × 单量", False),
    "store_top": ("门店 TOP10", False),
    "user_segment": ("用户价值分层", False),
    "hour_category": ("时段 × 品类热力", True),
}


def build_snapshot() -> dict:
    """一次性跑完全部取数，返回可直接落盘的快照。

    为什么要有快照：单个面板查询 2~8 秒，六个面板串起来 30 秒，
    大屏打开一次要等半分钟，答辩现场很难看。快照把「算」和「看」解耦：
    刷新时慢一次，之后每次打开都是毫秒级。
    """
    import time
    from datetime import datetime

    out: dict[str, Any] = {}
    timings: dict[str, float] = {}
    for name, (title, slow) in PANELS.items():
        t0 = time.time()
        try:
            out[name] = globals()[name]()
            out[name]["title"] = title
            timings[name] = round(time.time() - t0, 2)
        except Exception as e:  # noqa: BLE001
            # 单个面板失败不能拖垮整块大屏：记下来，前端显示「该面板数据缺失」
            out[name] = {"title": title, "items": [], "error": str(e)[:200]}
            timings[name] = round(time.time() - t0, 2)

    out["meta"] = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "row_counts": {k: _count(out[k]) for k in PANELS},
        "timings": timings,
        "total_seconds": round(sum(timings.values()), 2),
        "backend": "hive",
        "warehouse": "ydsh_ads / ydsh_dws / ydsh_dwd",
    }
    return out


def _count(panel: dict) -> int:
    if not isinstance(panel, dict):
        return 0
    if "items" in panel:
        return len(panel["items"])
    if "dates" in panel:
        return len(panel["dates"])
    if "hours" in panel:
        return len(panel["data"])
    return 0


if __name__ == "__main__":
    import json
    import time

    t = time.time()
    snap = build_snapshot()
    print(json.dumps(snap["meta"], ensure_ascii=False, indent=2))
    print(f"总耗时 {time.time() - t:.1f}s")
