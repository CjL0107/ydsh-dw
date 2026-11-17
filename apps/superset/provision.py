#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 4 周 · 子任务 1：Superset 数据源、数据集、图表、仪表板的脚本化创建
================================================
为什么不点鼠标建：
    手点出来的仪表板只存在 Superset 的元数据库里，换台机器就没了，
    "一键部署"就成了空话。这里用 REST API 把整套东西写成可重跑的脚本。

流程：
    1. 登录拿 access_token + csrf_token（Superset 写接口两者都要，缺一个 400）
    2. 建数据源：MySQL（ADS 集市，图表用）+ Hive（明细，即席查询用）
    3. 建数据集，并补指标定义（POST 不接受 metrics，必须再 PUT 一次）
    4. 建图表：折线、柱状、省份排行、饼图 ×2、明细表
    5. 建仪表板，12 栅格布局

关于登录方式（踩过的坑，写在这里免得重复）：
    用 JWT（POST /security/login 拿 access_token + Authorization: Bearer）时，
    建数据源、建数据集都能过，但建图表报 500：
        AttributeError: 'AnonymousUserMixin' object has no attribute '_sa_instance_state'
    同一个 token 去读 GET /api/v1/database/ 也只返回 count=0（其实是没权限看不到）。
    根因是 JWT 场景下 g.user 没被解析成真实用户，写操作要挂 created_by 时就炸了。
    改成 Web 表单登录（POST /login/ 拿 session cookie）后，读写全部正常。
    所以这里故意不用 JWT —— 不是它不好，是它在当前版本的写路径上不认人。

用法：
    python apps/superset/provision.py            # 创建/补齐
    python apps/superset/provision.py --reset    # 清掉状态文件后重建
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

BASE = os.getenv("SUPERSET_URL", "http://127.0.0.1:8088")
USER = os.getenv("SUPERSET_ADMIN", "admin")
PWD = os.getenv("SUPERSET_PASSWORD", "admin123")
MYSQL_PWD = os.getenv("MYSQL_ROOT_PASSWORD", "root")

DB_MYSQL = "MySQL · ADS 集市（图表数据源）"
DB_HIVE = "Hive · 数仓明细（即席查询）"
DASHBOARD = "云购生活 · 经营分析仪表板"

DATASETS = [
    ("ads_sales_daily_report", [("GMV", "SUM(gmv)"), ("有效订单", "SUM(paid_order_cnt)"), ("客单价", "AVG(aov)")]),
    ("ads_category_top10", [("GMV", "SUM(gmv)"), ("订单数", "SUM(order_cnt)")]),
    ("ads_province_top10", [("GMV", "SUM(gmv)"), ("订单数", "SUM(order_cnt)")]),
    ("ads_payment_share", [("GMV", "SUM(gmv)"), ("订单数", "SUM(orders)")]),
    ("ads_store_top10", [("GMV", "SUM(gmv)"), ("订单数", "SUM(order_cnt)")]),
    ("ads_kpi_summary", [("指标值", "MAX(metric_value)")]),
    ("ads_channel_contribution", [("GMV", "SUM(gmv)")]),
    ("ads_hour_category", [("GMV", "SUM(gmv)")]),
]

CHARTS = [
    {
        "name": "① 全年 GMV 趋势",
        "dataset": "ads_sales_daily_report",
        "viz_type": "echarts_timeseries_line",
        "params": {
            "viz_type": "echarts_timeseries_line", "x_axis": "stat_date",
            "metrics": ["GMV"], "groupby": [], "time_range": "No filter",
            "row_limit": 1000, "adhoc_filters": [],
            "x_axis_title": "日期", "y_axis_title": "GMV（元）",
            "show_legend": True, "rich_tooltip": True,
        },
    },
    {
        "name": "② 品类 GMV 结构",
        "dataset": "ads_category_top10",
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "viz_type": "echarts_timeseries_bar", "x_axis": "category",
            "metrics": ["GMV"], "groupby": [], "time_range": "No filter",
            "row_limit": 10, "adhoc_filters": [],
            "x_axis_title": "品类", "y_axis_title": "GMV（元）", "show_legend": False,
        },
    },
    {
        "name": "③ 省份 GMV 排行（区域分布）",
        "dataset": "ads_province_top10",
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "viz_type": "echarts_timeseries_bar", "x_axis": "province",
            "metrics": ["GMV"], "groupby": [], "time_range": "No filter",
            "row_limit": 10, "adhoc_filters": [],
            "x_axis_title": "省份", "y_axis_title": "GMV（元）", "show_legend": False,
        },
    },
    {
        "name": "④ 支付方式占比",
        "dataset": "ads_payment_share",
        "viz_type": "pie",
        "params": {
            "viz_type": "pie", "groupby": ["pay_type"], "metric": "GMV",
            "time_range": "No filter", "row_limit": 10, "adhoc_filters": [],
            "donut": True, "show_labels": True, "show_legend": True,
            "label_type": "key_percent",
        },
    },
    {
        "name": "⑤ 门店 GMV TOP10",
        "dataset": "ads_store_top10",
        "viz_type": "table",
        "params": {
            "viz_type": "table", "query_mode": "raw",
            "all_columns": ["rank_no", "store_name", "city", "store_level", "gmv", "order_cnt", "aov"],
            "order_by_cols": ['["gmv", false]'],
            "row_limit": 10, "time_range": "No filter", "adhoc_filters": [],
            "show_cell_bars": True,
        },
    },
    {
        "name": "⑥ 渠道 GMV 占比",
        "dataset": "ads_channel_contribution",
        "viz_type": "pie",
        "params": {
            "viz_type": "pie", "groupby": ["channel"], "metric": "GMV",
            "time_range": "No filter", "row_limit": 10, "adhoc_filters": [],
            "donut": True, "show_labels": True, "show_legend": True,
            "label_type": "key_percent",
        },
    },
]


class Superset:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.token = ""
        self.csrf = ""

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.csrf:
            h["X-CSRFToken"] = self.csrf
        return h

    def login(self) -> None:
        """表单登录拿 session cookie。

        注意：不能只用 JWT。JWT 下 g.user 是匿名用户，写图表时
        ChartDAO.create 要挂 created_by 会抛 AnonymousUserMixin 异常，
        读接口也会因为无权限而返回空列表。
        """
        page = self.s.get(f"{BASE}/login/", timeout=30)
        m = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', page.text)
        if not m:
            raise RuntimeError("登录页里没找到 csrf_token，页面结构可能变了")
        r = self.s.post(f"{BASE}/login/",
                        data={"username": USER, "password": PWD, "csrf_token": m.group(1)},
                        allow_redirects=False, timeout=30)
        if r.status_code not in (302, 200):
            raise RuntimeError(f"表单登录失败：{r.status_code} {r.text[:200]}")
        self.csrf = self.s.get(f"{BASE}/api/v1/security/csrf_token/",
                               headers={"Content-Type": "application/json"}, timeout=30).json()["result"]

    def get(self, path: str) -> dict:
        r = self.s.get(f"{BASE}/api/v1/{path}", headers=self._headers(), timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def post(self, path: str, payload: dict) -> dict:
        r = self.s.post(f"{BASE}/api/v1/{path}", headers=self._headers(),
                        data=json.dumps(payload), timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    def put(self, path: str, payload: dict) -> dict:
        r = self.s.put(f"{BASE}/api/v1/{path}", headers=self._headers(),
                       data=json.dumps(payload), timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"PUT {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()


def find(items: list[dict], key: str, value: str) -> dict | None:
    for it in items:
        if str(it.get(key, "")).strip() == value.strip():
            return it
    return None


def ensure_database(ss: "Superset", name: str, uri: str) -> int:
    payload = {
        "database_name": name, "sqlalchemy_uri": uri,
        "configuration_method": "sqlalchemy_form",
        "expose_in_sqllab": True, "allow_run_async": False,
        "allow_ctas": False, "allow_cvas": False, "allow_dml": False,
        "extra": json.dumps({"metadata_params": {}, "engine_params": {}}),
    }
    hit = find(ss.get("database/")["result"], "database_name", name)
    if hit:
        ss.put(f"database/{hit['id']}", payload)
        print(f"  [=] 数据源已更新：{name} (id={hit['id']})")
        return hit["id"]
    res = ss.post("database/", payload)
    print(f"  [+] 已创建数据源：{name} (id={res['id']})")
    return res["id"]


def ensure_dataset(ss: "Superset", db_id: int, table: str,
                   metrics: list[tuple[str, str]]) -> int:
    hit = find(ss.get("dataset/")["result"], "table_name", table)
    if hit:
        ds_id = hit["id"]
        print(f"  [=] 数据集已存在：{table} (id={ds_id})")
    else:
        res = ss.post("dataset/", {"database": db_id, "schema": "ydsh_ads", "table_name": table})
        ds_id = res["id"]
        print(f"  [+] 已创建数据集：{table} (id={ds_id})")
    # POST 不接受 metrics（返回 Unknown field.），必须单独 PUT 一次；
    # 重跑时指标已存在会返回 422 "One or more metrics already exist"，忽略即可
    try:
        ss.put(f"dataset/{ds_id}", {"metrics": [{"metric_name": n, "expression": e} for n, e in metrics]})
    except RuntimeError as e:
        if "already exist" not in str(e):
            raise
        print(f"      指标已存在，跳过：{table}")
    return ds_id


def ensure_chart(ss: "Superset", spec: dict, ds_id: int) -> int:
    payload = {
        "slice_name": spec["name"], "viz_type": spec["viz_type"],
        "datasource_id": ds_id, "datasource_type": "table",
        "params": json.dumps(spec["params"], ensure_ascii=False),
    }
    hit = find(ss.get("chart/")["result"], "slice_name", spec["name"])
    if hit:
        ss.put(f"chart/{hit['id']}", payload)
        print(f"  [=] 图表已更新：{spec['name']} (id={hit['id']})")
        return hit["id"]
    res = ss.post("chart/", payload)
    print(f"  [+] 已创建图表：{spec['name']} (id={res['id']})")
    return res["id"]


def build_position(chart_ids: list[int]) -> str:
    pos = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": DASHBOARD}},
    }
    for row_no in range(0, 6, 2):
        row_id = f"ROW-{row_no // 2 + 1}"
        children = []
        for idx in (row_no, row_no + 1):
            if idx >= len(chart_ids):
                continue
            cid = f"CHART-{chart_ids[idx]}"
            children.append(cid)
            pos[cid] = {
                "type": "CHART", "id": cid, "children": [],
                "meta": {"width": 6, "height": 50, "chartId": chart_ids[idx],
                         "uuid": f"chart-{chart_ids[idx]}", "sliceName": ""},
                "parents": ["ROOT_ID", "GRID_ID", row_id],
            }
        pos[row_id] = {"type": "ROW", "id": row_id, "children": children,
                       "meta": {"background": "BACKGROUND_TRANSPARENT"},
                       "parents": ["ROOT_ID", "GRID_ID"]}
        pos["GRID_ID"]["children"].append(row_id)
    return json.dumps(pos, ensure_ascii=False)


def ensure_dashboard(ss: "Superset", chart_ids: list[int]) -> int:
    payload = {
        "dashboard_title": DASHBOARD, "slug": "ydsh-sales-overview", "published": True,
        "position_json": build_position(chart_ids),
        "json_metadata": json.dumps({"chart_configuration": {}, "color_scheme": "supersetColors",
                                     "refresh_frequency": 0}),
    }
    hit = find(ss.get("dashboard/")["result"], "dashboard_title", DASHBOARD)
    if hit:
        ss.put(f"dashboard/{hit['id']}", payload)
        print(f"  [=] 仪表板已更新 (id={hit['id']})")
        return hit["id"]
    res = ss.post("dashboard/", payload)
    print(f"  [+] 已创建仪表板 (id={res['id']})")
    return res["id"]


def link_charts(board_id: int, chart_ids: list[int]) -> None:
    """把图表挂到仪表板上（dashboard_slices 关联表）。

    Superset 4.1 的 dashboard PUT 不接受 slices 字段（返回 Unknown field.），
    而 position_json 里的 meta.chartId 只是布局信息，不会自动建关联 ——
    结果就是看板打开后每个格子都提示"没有与此组件关联的图表定义"。
    UI 保存时由前端调接口补关联，脚本里没有这个入口，只能直接写关联表。
    这是本脚本里唯一一处绕过 REST 的地方，实测有效且幂等。
    """
    import pymysql

    conn = pymysql.connect(
        host=os.getenv("SUPERSET_META_HOST", "127.0.0.1"),
        port=int(os.getenv("SUPERSET_META_PORT", "3307")),
        user=os.getenv("MYSQL_USER", "root"), password=MYSQL_PWD,
        database=os.getenv("SUPERSET_DB", "superset"), autocommit=True,
    )
    cur = conn.cursor()
    cur.execute("DELETE FROM dashboard_slices WHERE dashboard_id=%s", (board_id,))
    cur.executemany("INSERT INTO dashboard_slices (dashboard_id, slice_id) VALUES (%s, %s)",
                    [(board_id, cid) for cid in chart_ids])
    cur.execute("SELECT COUNT(*) FROM dashboard_slices WHERE dashboard_id=%s", (board_id,))
    print(f"  [+] 图表关联已写入：{cur.fetchone()[0]} 个")
    cur.close()
    conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="清空状态文件后重建")
    args = ap.parse_args()
    if args.reset and STATE.exists():
        STATE.unlink()

    ss = Superset()
    ss.login()
    print(f"[ok] 登录成功 {BASE}（session cookie + csrf）")

    print("[1] 数据源")
    # 图表用 MySQL：ADS 结果 421 行，秒出。
    # 先试过 clickhouse+http，Superset 4.1 报 ClickHouseEngineSpec 加载失败
    # （sqlalchemy.dialects:clickhouse.http 找不到），不为一个驱动去改镜像。
    mysql_id = ensure_database(ss, DB_MYSQL,
                               f"mysql+pymysql://root:{MYSQL_PWD}@mysql:3306/ydsh_ads")
    try:
        ensure_database(ss, DB_HIVE,
                        "hive://hive@hive-server:10000/default")  # 默认认证即可；加 ?auth=NOSASL 会 TSocket read 0 bytes
        print("      Hive 数据源可用（即席查询走 pyhive，默认认证）")
    except RuntimeError as e:
        # Hive 驱动在本环境连不上（TSocket read 0 bytes），记录但不阻断：
        # 仪表板图表全部走 MySQL，Hive 连接只影响"即席查询明细"这一项
        print(f"  [!] Hive 数据源未建成：{str(e)[:160]}")

    print("[2] 数据集")
    ds_ids = {t: ensure_dataset(ss, mysql_id, t, m) for t, m in DATASETS}

    print("[3] 图表")
    chart_ids = []
    for spec in CHARTS:
        chart_ids.append(ensure_chart(ss, spec, ds_ids[spec["dataset"]]))

    print("[4] 仪表板")
    board_id = ensure_dashboard(ss, chart_ids)
    link_charts(board_id, chart_ids)

    print(f"\n完成：{BASE}/superset/dashboard/{board_id}/")
    print(f"图表 {len(chart_ids)} 个　数据集 {len(ds_ids)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
