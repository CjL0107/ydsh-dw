#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 4 周 · 子任务 3：Streamlit 对话式问答界面
================================================
链路：用户提问 → nl2sql.ask()（大模型生成 SQL / 规则兜底）
      → Hive 执行 → 表格展示 → 自动推断图表类型 → ECharts 渲染

四个刻意做的取舍：

1) **复用第 3 周的 nl2sql.ask()，不重写**。第 3 周已经打通了 Prompt、白名单校验、
   维度取值注入、报错回灌重试，这里再写一遍只会分裂成两套逻辑。
   界面层唯一的活儿是"把结果讲清楚"，不是重新实现一遍查数。

2) **图表类型自动推断，不让用户选**。业务同学问"广东省上月销售额"时，
   他没有义务知道自己要的是柱状图还是指标卡。规则很简单：
   结果只有 1 行 1 列 → 指标卡；有日期列 → 折线；1 个类目 + 1 个数值 → 柱状/饼图。

3) **SQL 永远可见**。折叠在 expander 里，但默认就在。NL2SQL 现在只有 7/8 正确率，
   把 SQL 藏起来等于让人盲信一个会错的模型。

4) **错误提示写"下一步做什么"，不写堆栈**。余额不足、容器没起、取值写错，
   这三种错误的处理动作完全不同，界面必须能区分。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from nl2sql import ask  # noqa: E402

# ECharts 走 API 服务（同机 8000 端口）而不是 CDN：
# 答辩现场断网也要能画图，CDN 拿不到就是一片空白
ECHARTS_URL = os.getenv("ECHARTS_URL", "http://127.0.0.1:8000/vendor/echarts.min.js")

EXAMPLES = [
    "2024 年总 GMV 是多少？",
    "广东省上月销售额是多少？",
    "客单价是多少？",
    "销量最高的前 5 个品类",
    "各渠道 GMV 占比",
    "支付方式分布",
]

st.set_page_config(page_title="云购生活 · 智能经营问答", page_icon="💬", layout="wide")


# ----------------------------------------------------------------------
# 图表推断
# ----------------------------------------------------------------------
DATE_HINTS = ("date", "日期", "月份", "月", "周", "day", "dt", "时间")


def _is_num(v) -> bool:
    try:
        float(str(v).replace(",", ""))
        return True
    except (TypeError, ValueError):
        return False


def infer_chart(rows: list[dict]) -> dict | None:
    """根据结果形状推断图表，返回 ECharts option 或 None（不适合画图）"""
    if not rows:
        return None
    cols = list(rows[0].keys())
    if len(cols) < 2:
        return None

    num_cols = [c for c in cols if all(_is_num(r.get(c)) for r in rows if r.get(c) not in (None, ""))]
    cat_cols = [c for c in cols if c not in num_cols]
    if not num_cols:
        return None

    # 超过 60 行不画：柱状图挤成一片，折线也会糊，不如看表
    if len(rows) > 60:
        rows = rows[:60]

    def val(r, c):
        try:
            return round(float(str(r.get(c)).replace(",", "")), 4)
        except (TypeError, ValueError):
            return None

    x_col = cat_cols[0] if cat_cols else cols[0]
    y_col = num_cols[0]
    x_data = [str(r.get(x_col)) for r in rows]

    looks_date = any(h in x_col.lower() for h in DATE_HINTS) or all(
        str(v)[:4].isdigit() and "-" in str(v) for v in x_data[:3]
    )

    if len(rows) == 1 and len(num_cols) == 1:
        return {"kind": "metric", "value": val(rows[0], y_col), "label": y_col}

    if looks_date:
        series = [{
            "name": c, "type": "line", "smooth": True, "showSymbol": False,
            "data": [val(r, c) for r in rows],
            "areaStyle": {"opacity": 0.18},
        } for c in num_cols[:2]]
        return {"kind": "line", "x": x_data, "series": series}

    # 「各渠道 GMV 占比」这种问题，模型会同时返回 gmv 和 gmv_share。
    # 全都画成柱子时四根柱高度几乎一样（GMV 本来就差不多），图等于没信息；
    # 占比列才是问题要的答案，优先用它画环形图。
    share_cols = [c for c in num_cols
                  if any(k in c.lower() for k in ("share", "rate", "pct", "占比", "比例"))]
    if share_cols and cat_cols and 2 <= len(rows) <= 12:
        c = share_cols[0]
        vals = [val(r, c) for r in rows]
        if all(v is not None and 0 <= v <= 100 for v in vals):
            # ADS 层占比存的是小数（0.2520），直接塞给饼图 tooltip 会显示 "0.252"，
            # 标签上却是 25.2%，两处对不上。统一乘 100 成百分数。
            k = 100 if max(vals) <= 1 else 1
            return {
                "kind": "pie", "name": c,
                "data": [{"name": x_data[i], "value": round(vals[i] * k, 2)}
                         for i in range(len(rows))],
            }

    if len(cat_cols) >= 1 and len(rows) <= 12 and len(num_cols) == 1:
        # 单一数值列且每行都小于 100，按占比处理画环形图（GMV 占比那类）
        vals = [val(r, y_col) for r in rows]
        if vals and max(v for v in vals if v is not None) <= 100:
            k = 100 if max(vals) <= 1 else 1
            return {
                "kind": "pie", "name": y_col,
                "data": [{"name": x_data[i], "value": round(vals[i] * k, 2)}
                         for i in range(len(rows))],
            }
        return {
            "kind": "bar", "x": x_data, "y_name": y_col,
            "y": vals,
        }

    return {
        "kind": "bar", "x": x_data, "y_name": y_col,
        "y": [val(r, y_col) for r in rows],
    }


def chart_html(option: dict, height: int = 360) -> str:
    payload = json.dumps(option, ensure_ascii=False)
    # 标签格式串必须用 json.dumps 编码后再拼进去。
    # 直接写在 f-string 里写 '{b}\n{d}%'，Python 会把 \n 变成真实换行，
    # 生成的 JS 就成了 'label: { formatter: '{b'
    # {d}%' }' —— 单引号字符串里出现换行，整段脚本 SyntaxError，
    # 表现是图表区域一片空白、页面又不报错（错误只在内联 script 里）。
    pie_fmt = json.dumps("{b}\n{d}%")
    return f"""
<div id="chart" style="width:100%;height:{height}px;"></div>
<script src="{ECHARTS_URL}"></script>
<script>
  const opt = {payload};
  if (typeof echarts === 'undefined') {{
    document.getElementById('chart').innerHTML =
      '<div style="color:#b93a4a;font:14px sans-serif;padding:20px">' +
      'ECharts 未加载：请确认数据服务已启动（默认 http://127.0.0.1:8000）</div>';
  }} else {{
    const chart = echarts.init(document.getElementById('chart'));
    const base = {{
      color: ['#2f8fd8', '#f0a63c', '#3fb98f', '#9b7fe0', '#d95f5f'],
      grid: {{ left: 78, right: 24, top: 46, bottom: 48 }},
      tooltip: {{ trigger: opt.kind === 'pie' ? 'item' : 'axis' }},
      textStyle: {{ fontFamily: 'Microsoft YaHei, sans-serif' }}
    }};
    let full;
    if (opt.kind === 'line') {{
      full = Object.assign(base, {{
        xAxis: {{ type: 'category', data: opt.x, axisLabel: {{ hideOverlap: true }} }},
        yAxis: {{ type: 'value', splitLine: {{ lineStyle: {{ color: '#eef2f7' }} }},
                 axisLabel: {{ formatter: (v) => Math.abs(v) >= 1e4 ? (v / 1e4) + '万' : v }} }},
        series: opt.series
      }});
    }} else if (opt.kind === 'pie') {{
      full = Object.assign(base, {{
        series: [{{
          type: 'pie', radius: ['42%', '68%'], center: ['50%', '52%'],
          label: {{ formatter: {pie_fmt}, fontSize: 12 }},
          data: opt.data
        }}]
      }});
    }} else {{
      full = Object.assign(base, {{
        xAxis: {{ type: 'category', data: opt.x, axisLabel: {{ interval: 0, rotate: opt.x.length > 8 ? 30 : 0 }} }},
        yAxis: {{ type: 'value', name: opt.y_name,
                 axisLabel: {{ formatter: (v) => Math.abs(v) >= 1e4 ? (v / 1e4) + '万' : v }} }},
        series: [{{ type: 'bar', data: opt.y, barMaxWidth: 34,
                   itemStyle: {{ borderRadius: [4, 4, 0, 0] }} }}]
      }});
    }}
    chart.setOption(full);
    window.addEventListener('resize', () => chart.resize());
  }}
</script>
"""


# ----------------------------------------------------------------------
# 错误提示：按错误类型给出下一步动作
# ----------------------------------------------------------------------
def explain_error(msg: str) -> str:
    m = (msg or "").lower()
    if "容器不可用" in msg or "docker" in m or "beeline" in m:
        return "**Hive 连接失败**。检查：`docker ps` 里 hive-server 是否 Up，然后 `curl :10000` 是否可连。"
    if "402" in msg or "insufficient" in m or "余额" in msg:
        return "**大模型账户余额不足**。可先用侧边栏的「仅规则模式」演示链路，或充值后重试。"
    if "401" in msg or "unauthorized" in m or "api key" in m:
        return "**API Key 无效**。检查 `.env` 里的 `LLM_API_KEY` 是否粘贴完整（不要带引号或空格）。"
    if "429" in msg:
        return "**触发限流**。等 10 秒重试；批量评测时应在客户端加间隔。"
    if "语义" in msg or "semantic" in m or "cannot resolve" in m or "invalid column" in m:
        return "**SQL 语义错误**（字段/表名写错）。模型会在下一次重试里自我修正，若仍失败可换个说法提问。"
    if "取值" in msg:
        return "**维度取值写错**。比如 `province='广东'` 而库里是「广东省」，见侧边栏的维度取值说明。"
    return "查询失败。展开下方原始报错看具体原因。"


# ----------------------------------------------------------------------
# 单条回答渲染
# ----------------------------------------------------------------------
def render_answer(item: dict) -> None:
    if not item.get("ok"):
        st.error(item.get("error") or "查询失败")
        st.markdown(explain_error(item.get("error", "")))
        if item.get("sql"):
            with st.expander("失败的 SQL（便于排查）", expanded=False):
                st.code(item["sql"], language="sql")
        return

    rows = item.get("rows") or []
    sql = item.get("sql") or ""
    elapsed = item.get("elapsed", 0)
    mode = "大模型生成" if item.get("mode") == "llm" else "规则兜底"

    c1, c2, c3 = st.columns([2, 2, 6])
    c1.metric("返回行数", len(rows))
    c2.metric("耗时", f"{elapsed:.1f}s")
    c3.caption(f"生成方式：{mode}　·　执行引擎：HiveServer2")

    if not rows:
        # 0 行是 NL2SQL 最典型的"假成功"：SQL 没错、执行成功、就是没数据。
        # 界面必须点出来，而不是显示一个空表格让用户以为系统坏了。
        st.warning("SQL 执行成功，但返回 0 行。常见原因：维度取值与库里不一致（如「广东」vs「广东省」）、"
                   "或时间范围超出数据区间（本数据集为 2024 全年）。")
    else:
        option = infer_chart(rows)
        if option and option["kind"] == "metric":
            st.metric(option["label"], f"{option['value']:,}" if option["value"] is not None else "—")
        elif option:
            components.html(chart_html(option), height=380)

        with st.expander(f"结果明细（{len(rows)} 行）", expanded=len(rows) <= 8):
            st.dataframe(rows, use_container_width=True, height=min(320, 36 * (len(rows) + 1)))

        csv = "\n".join([",".join(rows[0].keys())] +
                        [",".join(str(r.get(k, "")) for k in rows[0].keys()) for r in rows])
        st.download_button("下载 CSV", csv.encode("utf-8-sig"), file_name="nl2sql_result.csv",
                           mime="text/csv", key=f"dl-{item['id']}")

    with st.expander("生成的 SQL", expanded=False):
        st.code(sql, language="sql")


# ----------------------------------------------------------------------
# 侧边栏
# ----------------------------------------------------------------------
def sidebar() -> dict:
    with st.sidebar:
        st.markdown("### 云购生活 · 智能经营问答")
        st.caption("自然语言 → SQL → Hive → 图表")

        offline = st.toggle("仅规则模式（不调大模型）", value=not os.getenv("LLM_API_KEY"),
                            help="关闭时优先用大模型；大模型余额不足或断网时可打开，链路仍可演示")
        show_tip = st.toggle("显示口径提示", value=True)

        st.divider()
        st.markdown("**示例问题**")
        picked = None
        for q in EXAMPLES:
            if st.button(q, use_container_width=True, key=f"ex-{q}"):
                picked = q

        st.divider()
        if st.button("清空对话", use_container_width=True):
            st.session_state.history = []
            st.rerun()

        st.divider()
        if show_tip:
            st.markdown(
                "**口径提示**\n\n"
                "- 有效订单 = 已支付 / 已完成 / 退款中\n"
                "- 时间范围为 2024 全年\n"
                "- 省份带「省」后缀（广东省），城市不带「市」（深圳）"
            )
        st.caption(f"ECharts: {ECHARTS_URL}")
    return {"offline": offline, "picked": picked}


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main() -> None:
    st.title("💬 智能经营问答")
    st.caption("用中文提问，系统生成 SQL 并在 Hive 上执行；结果自动选图。")

    cfg = sidebar()
    if "history" not in st.session_state:
        st.session_state.history = []

    # 首屏引导：没有对话时给三条示例，比空白页友好
    if not st.session_state.history:
        st.info("试试问：「2024 年总 GMV 是多少？」「广东省上月销售额是多少？」「各渠道 GMV 占比」")

    question = cfg["picked"]
    typed = st.chat_input("输入你的问题，例如：广东省上月销售额是多少？")
    question = typed or question

    if question:
        t0 = time.time()
        with st.spinner("生成 SQL 并查询 Hive…"):
            try:
                res = ask(question, offline=cfg["offline"], verbose=False)
                item = {
                    "id": len(st.session_state.history),
                    "question": question,
                    "ok": res.get("ok"),
                    "sql": res.get("sql", ""),
                    "rows": res.get("rows") or [],
                    "error": res.get("error"),
                    "mode": (res.get("trace") or {}).get("mode", "offline"),
                    "elapsed": round(time.time() - t0, 2),
                }
            except Exception as e:  # noqa: BLE001
                item = {
                    "id": len(st.session_state.history),
                    "question": question, "ok": False, "sql": "",
                    "rows": [], "error": str(e)[:400], "mode": "offline",
                    "elapsed": round(time.time() - t0, 2),
                }
        st.session_state.history.append(item)

    for item in st.session_state.history:
        with st.chat_message("user"):
            st.markdown(item["question"])
        with st.chat_message("assistant"):
            render_answer(item)


if __name__ == "__main__":
    main()
