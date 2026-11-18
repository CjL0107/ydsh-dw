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
    # 可视化宽表：所有仪表板图表都挂这一个数据集。
    # 原来是一图一表（8 张 ADS 结果表各管一个面板），字段互不相交，
    # 交叉筛选没有公共维度可下发 —— 界面上标签出来了、查询也重发了，图却不变化。
    ("ads_viz_wide", [
        ("GMV", "SUM(pay_amount)"),
        ("订单数", "COUNT(DISTINCT order_id)"),
        ("用户数", "COUNT(DISTINCT user_id)"),
        ("件数", "SUM(quantity)"),
    ]),
    ("ads_sales_daily_report", [("GMV", "SUM(gmv)"), ("有效订单", "SUM(paid_order_cnt)"), ("客单价", "AVG(aov)")]),
    ("ads_category_top10", [("GMV", "SUM(gmv)"), ("订单数", "SUM(order_cnt)")]),
    ("ads_province_top10", [("GMV", "SUM(gmv)"), ("订单数", "SUM(order_cnt)")]),
    ("ads_payment_share", [("GMV", "SUM(gmv)"), ("订单数", "SUM(orders)")]),
    ("ads_store_top10", [("GMV", "SUM(gmv)"), ("订单数", "SUM(order_cnt)")]),
    ("ads_kpi_summary", [("指标值", "MAX(metric_value)")]),
    ("ads_channel_contribution", [("GMV", "SUM(gmv)")]),
    ("ads_hour_category", [("GMV", "SUM(gmv)")]),
]

WIDE = "ads_viz_wide"

CHARTS = [
    {
        "name": "① 全年 GMV 趋势",
        "dataset": WIDE,
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
        "dataset": WIDE,
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "viz_type": "echarts_timeseries_bar", "x_axis": "category",
            "metrics": ["GMV"], "groupby": [], "time_range": "No filter",
            "row_limit": 20, "adhoc_filters": [], "order_desc": True,
            "x_axis_title": "品类", "y_axis_title": "GMV（元）", "show_legend": False,
        },
    },
    {
        "name": "③ 省份 GMV 排行（区域分布）",
        "dataset": WIDE,
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "viz_type": "echarts_timeseries_bar", "x_axis": "province",
            "metrics": ["GMV"], "groupby": [], "time_range": "No filter",
            "row_limit": 20, "adhoc_filters": [], "order_desc": True,
            "x_axis_title": "省份", "y_axis_title": "GMV（元）", "show_legend": False,
        },
    },
    {
        "name": "④ 支付方式占比",
        "dataset": WIDE,
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
        "dataset": WIDE,
        "viz_type": "table",
        "params": {
            "viz_type": "table", "query_mode": "aggregate",
            "groupby": ["store_name", "city", "store_level"],
            "metrics": ["GMV", "订单数"],
            "order_by_cols": ['["GMV", false]'],
            "row_limit": 10, "time_range": "No filter", "adhoc_filters": [],
            "show_cell_bars": True,
        },
    },
    {
        "name": "⑥ 渠道 GMV 占比",
        "dataset": WIDE,
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


def ensure_count_metric(ds_id: int) -> None:
    """给数据集补一个 count 指标。

    原生筛选器初始化时要拿数据集的 count 指标来算"有多少可选值"，
    数据集没有这条指标时筛选器会直接报
        Error: Metric 'count' does not exist
    而 Superset 4.1 的 PUT /dataset 补 count 会被 422
        "One or more metrics already exist"
    挡住（它要求一次提交全部指标，且不允许重名）。所以只能直接插表。
    """
    import pymysql

    conn = pymysql.connect(
        host=os.getenv("SUPERSET_META_HOST", "127.0.0.1"),
        port=int(os.getenv("SUPERSET_META_PORT", "3307")),
        user=os.getenv("MYSQL_USER", "root"), password=MYSQL_PWD,
        database=os.getenv("SUPERSET_DB", "superset"), autocommit=True,
    )
    cur = conn.cursor()
    cur.execute("SELECT id FROM sql_metrics WHERE table_id=%s AND metric_name='count'", (ds_id,))
    if cur.fetchone():
        print(f"  [=] count 指标已存在（dataset {ds_id}）")
    else:
        cur.execute(
            "INSERT INTO sql_metrics (metric_name, expression, metric_type, table_id, "
            "created_on, changed_on) VALUES ('count','COUNT(*)','count',%s, NOW(), NOW())",
            (ds_id,),
        )
        print(f"  [+] 已补 count 指标（dataset {ds_id}）")
    cur.close()
    conn.close()


def build_filters(ds_id: int, chart_ids: list[int]) -> list[dict]:
    """仪表板左侧的原生筛选器。

    为什么要脚本化：筛选器只存在 json_metadata 里，手点出来的换台机器就没了。
    为什么时间筛选器要 defaultDataMask=No filter：给死默认值的话，
    第一次打开看板就只剩一个时间窗，演示时容易被误认为数据缺失。

    filter_select 的 targets 必须指向「图表实际用的那个数据集」。
    之前图表各用各的表，筛选器只能命中其中一张，现在全部落在 ads_viz_wide 上。
    """
    scope = {"rootPath": ["ROOT_ID"], "excluded": []}

    def value_filter(fid: str, name: str, column: str) -> dict:
        return {
            "id": fid, "name": name, "filterType": "filter_select",
            "targets": [{"datasetId": ds_id, "column": {"name": column}}],
            "defaultDataMask": {"extraFormData": {}, "filterState": {"value": None}, "ownState": {}},
            "controlValues": {
                "enableEmptyFilter": False, "multiSelect": True,
                "sortAscending": True, "canSelectAll": True,
                "defaultToFirstItem": False, "inverseSelection": False,
                "searchAllOptions": True, "canCreateNew": False, "creatable": False,
            },
            "scope": scope, "chartsInScope": chart_ids, "tabsInScope": [],
            "type": "NATIVE_FILTER", "description": "",
        }

    return [
        {
            "id": "NATIVE_FILTER-time-1", "name": "统计日期", "filterType": "filter_time",
            "targets": [{"datasetId": ds_id, "column": {"name": "stat_date"}}],
            "defaultDataMask": {"extraFormData": {},
                                "filterState": {"value": "No filter"}, "ownState": {}},
            "controlValues": {"enableEmptyFilter": False},
            "scope": scope, "chartsInScope": chart_ids, "tabsInScope": [],
            "type": "NATIVE_FILTER", "description": "",
        },
        value_filter("NATIVE_FILTER-category", "品类", "category"),
        value_filter("NATIVE_FILTER-province", "省份", "province"),
        value_filter("NATIVE_FILTER-paytype", "支付方式", "pay_type"),
    ]


def ensure_dashboard(ss: "Superset", chart_ids: list[int], ds_id: int) -> int:
    meta = {
        "chart_configuration": {},
        "color_scheme": "supersetColors",
        "refresh_frequency": 0,
        # 交叉筛选必须显式打开：默认是关的，关着的时候点图不会有任何反应
        "cross_filters_enabled": True,
        "timed_refresh_immune_slices": [],
        "expanded_slices": {},
        "label_colors": {},
        "shared_label_colors": {},
        "color_scheme_domain": [],
        "native_filter_configuration": build_filters(ds_id, chart_ids),
    }
    payload = {
        "dashboard_title": DASHBOARD, "slug": "ydsh-sales-overview", "published": True,
        "position_json": build_position(chart_ids),
        "json_metadata": json.dumps(meta, ensure_ascii=False),
    }
    hit = find(ss.get("dashboard/")["result"], "dashboard_title", DASHBOARD)
    if hit:
        ss.put(f"dashboard/{hit['id']}", payload)
        print(f"  [=] 仪表板已更新 (id={hit['id']})，筛选器 {len(meta['native_filter_configuration'])} 个")
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


def grant_public_read(dataset_ids: list[int]) -> None:
    """让 Public 角色能读「本项目的这几个数据集」，从而免登录看看板。

    为什么必须单独做这一步：
        PUBLIC_ROLE_LIKE = "Gamma" 只把 Public 角色的菜单/读写权限补成 Gamma，
        而 Gamma 对**数据集**是空的 —— 数据集权限是按表逐条授予的
        （每条是 view_menu 里的 `[库].[表](id:N)` + `datasource_access`）。
        Superset 判定非管理员能否看看板的规则是
        「看板里至少有一个能访问的数据集」，所以只补 Gamma 的结果是：
        看板页 302 回列表、列表 count=0，表现就是"没有数据"。

    权限边界（最小化）：
        只给仪表板实际用到的这 8 张 ADS 结果表，不给 all_datasource_access，
        明细层 dwd/dws 仍然读不到；匿名只读，没有 can_write。
    """
    import pymysql

    conn = pymysql.connect(
        host=os.getenv("SUPERSET_META_HOST", "127.0.0.1"),
        port=int(os.getenv("SUPERSET_META_PORT", "3307")),
        user=os.getenv("MYSQL_USER", "root"), password=MYSQL_PWD,
        database=os.getenv("SUPERSET_DB", "superset"), autocommit=True,
    )
    cur = conn.cursor()
    # view_menu 的名字形如 "[MySQL · ADS 结果层].[ads_kpi_summary](id:7)"，
    # 库里带中文和点号，直接按名字匹配容易踩坑，用结尾的 (id:N) 定位更稳。
    granted = 0
    for ds_id in dataset_ids:
        cur.execute(
            """
            INSERT IGNORE INTO ab_permission_view_role (permission_view_id, role_id)
            SELECT pv.id, r.id
            FROM ab_permission_view pv
            JOIN ab_permission p ON p.id = pv.permission_id
            JOIN ab_view_menu vm ON vm.id = pv.view_menu_id
            CROSS JOIN ab_role r
            WHERE p.name = 'datasource_access'
              AND r.name = 'Public'
              AND vm.name LIKE %s
            """,
            (f"%(id:{ds_id})",),
        )
        granted += cur.rowcount
    cur.execute(
        """
        SELECT COUNT(*) FROM ab_permission_view_role pvr
        JOIN ab_role r ON r.id = pvr.role_id
        JOIN ab_permission_view pv ON pv.id = pvr.permission_view_id
        JOIN ab_permission p ON p.id = pv.permission_id
        WHERE r.name = 'Public' AND p.name = 'datasource_access'
        """
    )
    total = cur.fetchone()[0]
    print(f"  [+] Public 数据集读权限：新增 {granted} 条，累计 {total} 条")
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
    ensure_count_metric(ds_ids[WIDE])
    board_id = ensure_dashboard(ss, chart_ids, ds_ids[WIDE])
    link_charts(board_id, chart_ids)

    print("[5] 免登录查看（Public 角色）")
    grant_public_read(list(ds_ids.values()))

    print(f"\n完成：{BASE}/superset/dashboard/{board_id}/")
    print(f"图表 {len(chart_ids)} 个　数据集 {len(ds_ids)} 个")
    print("免登录模板：/superset/dashboard/ydsh-sales-overview/（公开只读）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
