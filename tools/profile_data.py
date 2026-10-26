# -*- coding: utf-8 -*-
"""
profile_data.py —— 数据质量预评估脚本（离线版）

用途：在导入 Hive 之前，先用 Python 把 6 张 CSV 的脏数据摸清楚，
     产出与 Hive SQL 口径一致的《数据质量预评估表》基线数字。

为什么需要它：
  1. 建表前必须知道每列真实类型与脏值形态，否则 DDL 会写错；
  2. 清洗 SQL 写完要有"预期结果"，才能在 Hive 里核对是否正确；
  3. 后续写《数据质量预评估表》需要一个可复现的统计来源。

运行：
  python tools/profile_data.py --data-dir "C:/Users/Lenovo/Desktop/企业项目实训/模拟数据"

依赖：仅标准库（csv / collections / re），无需 pandas。
"""
import argparse
import csv
import io
import os
import re
from collections import Counter, OrderedDict

csv.field_size_limit(10 * 1024 * 1024)

# ---------------------------------------------------------------- 表结构定义
# 主键 / 枚举列 / 日期列 / 数值列 的声明，用于定向扫描
TABLE_SPEC = OrderedDict({
    "ods_user": {
        "pk": "user_id",
        "enum": ["gender", "member_level", "province"],
        "date": ["register_date"],
        "num": ["age"],
        "phone": ["phone"],
    },
    "ods_product": {
        "pk": "product_id",
        "enum": ["category", "status"],
        "date": ["launch_date"],
        "num": ["price", "cost", "stock"],
        "phone": [],
    },
    "ods_store": {
        "pk": "store_id",
        "enum": ["province"],
        "date": ["open_date"],
        "num": ["area"],
        "phone": [],
    },
    "ods_order": {
        "pk": "order_id",
        "enum": ["pay_type", "order_status", "channel"],
        "date": ["order_date"],
        "num": ["pay_amount", "discount_amount"],
        "phone": [],
    },
    "ods_order_item": {
        "pk": "item_id",
        "enum": [],
        "date": [],
        "num": ["quantity", "unit_price", "discount"],
        "phone": [],
    },
    "ods_user_behavior": {
        "pk": "behavior_id",
        "enum": ["behavior_type"],
        "date": ["behavior_time"],
        "num": [],
        "phone": [],
    },
})

DATE_PATTERNS = [
    ("yyyy-MM-dd", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("yyyy/MM/dd", re.compile(r"^\d{4}/\d{2}/\d{2}$")),
    ("yyyyMMdd", re.compile(r"^\d{8}$")),
    ("yyyy-MM-dd HH:mm:ss", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")),
    ("yyyy/M/d", re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")),
    ("yyyy年M月d日", re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日$")),
    ("脏值/其他", None),
]


def read_csv(path):
    """读取 CSV，返回 (header, rows)；自动剥离 UTF-8 BOM。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return [], []
    header = [h.strip().lstrip("\ufeff") for h in rows[0]]
    body = [r for r in rows[1:] if any(c.strip() for c in r)]
    return header, body


def is_blank(v):
    return v is None or str(v).strip() in ("", "NULL", "null", "None", "\\N", "NaN")


def classify_date(v):
    for name, pat in DATE_PATTERNS:
        if pat and pat.match(v):
            return name
    return "脏值/其他"


def profile_table(path, name, spec):
    header, rows = read_csv(path)
    n = len(rows)
    out = {
        "table": name,
        "rows": n,
        "cols": len(header),
        "header": header,
        "col_stats": [],
        "pk_dup_rows": 0,
        "pk_dup_groups": 0,
        "full_dup_rows": 0,
        "issues": [],
    }

    idx = {c: i for i, c in enumerate(header)}

    def col(i):
        return [(r[i] if i < len(r) else "") for r in rows]

    # ---- 逐列基础统计
    for i, c in enumerate(header):
        vals = col(i)
        blanks = sum(1 for v in vals if is_blank(v))
        cnt = Counter(v.strip() for v in vals if not is_blank(v))
        stat = {
            "col": c,
            "blank": blanks,
            "blank_rate": round(blanks / n * 100, 2) if n else 0,
            "distinct": len(cnt),
            "top": cnt.most_common(8),
        }
        out["col_stats"].append(stat)

    # ---- 主键重复
    pk = spec.get("pk")
    if pk and pk in idx:
        keys = col(idx[pk])
        c = Counter(k.strip() for k in keys)
        dup_keys = {k: v for k, v in c.items() if v > 1 and not is_blank(k)}
        out["pk_dup_groups"] = len(dup_keys)
        out["pk_dup_rows"] = sum(dup_keys.values())
        out["pk_dup_excess"] = sum(v - 1 for v in dup_keys.values())
        out["pk_dup_sample"] = list(dup_keys.items())[:5]

    # ---- 整行完全重复
    seen = Counter(tuple(x.strip() for x in r) for r in rows)
    out["full_dup_rows"] = sum(v - 1 for v in seen.values() if v > 1)

    # ---- 枚举列取值分布
    out["enums"] = {}
    for c in spec.get("enum", []):
        if c in idx:
            vals = [v.strip() for v in col(idx[c])]
            cnt = Counter(vals)
            out["enums"][c] = cnt.most_common(20)

    # ---- 日期列格式分布
    out["dates"] = {}
    for c in spec.get("date", []):
        if c in idx:
            vals = [v.strip() for v in col(idx[c]) if not is_blank(v)]
            cnt = Counter(classify_date(v) for v in vals)
            out["dates"][c] = cnt.most_common()

    # ---- 数值列异常
    out["nums"] = {}
    for c in spec.get("num", []):
        if c in idx:
            vals = col(idx[c])
            nums, bad, neg, zero = [], 0, 0, 0
            for v in vals:
                v = v.strip()
                if is_blank(v):
                    continue
                try:
                    f = float(v)
                    nums.append(f)
                    if f < 0:
                        neg += 1
                    if f == 0:
                        zero += 1
                except ValueError:
                    bad += 1
            out["nums"][c] = {
                "非数值": bad,
                "负数": neg,
                "零值": zero,
                "最小": min(nums) if nums else None,
                "最大": max(nums) if nums else None,
            }

    # ---- 手机号格式
    out["phones"] = {}
    for c in spec.get("phone", []):
        if c in idx:
            vals = [v.strip() for v in col(idx[c]) if not is_blank(v)]
            pure = sum(1 for v in vals if re.fullmatch(r"1\d{10}", v))
            out["phones"][c] = {
                "非空数": len(vals),
                "规范11位纯数字": pure,
                "不规范": len(vals) - pure,
                "样例": [v for v in vals if not re.fullmatch(r"1\d{10}", v)][:5],
            }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", default=None, help="报告输出路径（.md）")
    args = ap.parse_args()

    lines = []
    w = lines.append
    w("# 数据质量预评估报告（离线基线）\n")
    w(f"数据目录：`{args.data_dir}`\n")

    for name, spec in TABLE_SPEC.items():
        path = os.path.join(args.data_dir, name + ".csv")
        if not os.path.exists(path):
            w(f"\n> 缺少文件：{path}\n")
            continue
        r = profile_table(path, name, spec)
        w(f"\n## {name}　{r['rows']} 行 × {r['cols']} 列\n")
        w("| 字段 | 空值数 | 空值率 | 去重取值数 |")
        w("|---|---|---|---|")
        for s in r["col_stats"]:
            flag = " ⚠️" if s["blank"] else ""
            w(f"| {s['col']}{flag} | {s['blank']} | {s['blank_rate']}% | {s['distinct']} |")

        w("")
        w(f"- 主键重复：**{r['pk_dup_groups']} 组 / 涉及 {r['pk_dup_rows']} 行**（多出 {r.get('pk_dup_excess', 0)} 行需去重）")
        w(f"- 整行完全重复：**{r['full_dup_rows']} 行**")
        for c, top in r["enums"].items():
            vals = "、".join(f"`{k}`({v})" for k, v in top)
            w(f"- 枚举 `{c}` 取值：{vals}")
        for c, top in r["dates"].items():
            vals = "、".join(f"`{k}`({v})" for k, v in top)
            w(f"- 日期 `{c}` 格式分布：{vals}")
        for c, d in r["nums"].items():
            w(f"- 数值 `{c}`：非数值 {d['非数值']}、负数 {d['负数']}、零值 {d['零值']}、范围 [{d['最小']}, {d['最大']}]")
        for c, d in r["phones"].items():
            w(f"- 手机号 `{c}`：非空 {d['非空数']}，不规范 {d['不规范']} 例，样例 {d['样例']}")

    report = "\n".join(lines)
    print(report)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n[已写入] {args.out}")


if __name__ == "__main__":
    main()
