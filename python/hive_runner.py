#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Hive 执行器 —— 所有 NL2SQL 的 SQL 都通过这里真正执行
====================================================
实现方式：docker exec 进入 hive-server 容器，用 beeline 以 CSV 输出执行 SQL。
不直接依赖 pyhive / thrift：Windows 下 thrift_sasl 编译依赖较多，且本项目
的 Hive 跑在容器里，走 beeline 更贴近实际运维方式，也便于复现。

安全约束（务必保留）：
  - 只允许单条 SELECT / WITH 查询，拒绝 DDL / DML
  - 拒绝分号拼接的多语句
  - 限制返回行数上限，避免大模型生成全表扫描把容器拖死
"""
from __future__ import annotations

import csv
import io
import re
import subprocess
from typing import Any


CONTAINER = "hive-server"
HIVE_URL = "jdbc:hive2://localhost:10000"
MAX_ROWS = 200


class SqlRejected(Exception):
    """SQL 未通过安全校验"""


def _normalize(sql: str) -> str:
    s = sql.strip()
    s = re.sub(r"^```(?:sql)?\s*", "", s, flags=re.I)      # 去掉模型爱加的代码围栏
    s = re.sub(r"\s*```$", "", s)
    s = s.rstrip(";").strip()
    return s


def validate_sql(sql: str, allowed_tables: set[str] | None = None) -> str:
    """返回清洗后的 SQL；不合规直接抛异常，绝不放行"""
    s = _normalize(sql)
    if not s:
        raise SqlRejected("SQL 为空")

    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")):
        raise SqlRejected("只允许 SELECT / WITH 查询")

    # 多语句检查：去掉字符串字面量后再看分号
    stripped = re.sub(r"'[^']*'", "''", s)
    if ";" in stripped:
        raise SqlRejected("检测到多条语句，已拒绝执行")

    banned = ["insert", "update", "delete", "drop", "alter", "create",
              "truncate", "grant", "revoke", "load", "overwrite", "msck"]
    for kw in banned:
        if re.search(rf"\b{kw}\b", low):
            raise SqlRejected(f"检测到禁止的关键字：{kw}")

    if allowed_tables:
        # 抽取 from / join 后面出现的库.表
        refs = set(re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w]*\.[a-zA-Z_][\w]*)", low))
        unknown = {r for r in refs if r not in allowed_tables}
        if unknown:
            raise SqlRejected(f"引用了未授权的表：{sorted(unknown)}")
    return s


def run_sql(sql: str, timeout: int = 600, max_rows: int | None = None) -> list[dict[str, Any]]:
    """执行单条 SQL，返回 list[dict]（CSV 表头做 key）

    max_rows：默认走 MAX_ROWS 安全上限；批处理脚本（如全量日序列）可显式放宽，
    但仍需调用方自己保证 SQL 有 LIMIT 或聚合，避免全表扫描拖死容器。
    """
    limit_rows = max_rows or MAX_ROWS
    s = _normalize(sql)
    cmd = [
        "docker", "exec", "-i", CONTAINER,
        "beeline", "-u", HIVE_URL, "-n", "root",
        "--silent=true", "--outputformat=csv2", "-f", "/dev/stdin",
    ]
    proc = subprocess.run(
        cmd, input=s, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    out = proc.stdout or ""
    if "Error while compiling" in out or "FAILED:" in out or "ParseException" in out:
        raise RuntimeError(_extract_error(out))

    lines = [ln for ln in out.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith(("SLF4J", "log4j", "[WARN]", "No such file"))]
    if not lines:
        return []

    header = next(csv.reader([lines[0]]))
    rows: list[dict[str, Any]] = []
    for ln in lines[1:]:
        try:
            vals = next(csv.reader([ln]))
        except Exception:
            continue
        if len(vals) == len(header):
            rows.append(dict(zip(header, vals)))
        if len(rows) >= limit_rows:
            break
    return rows


def _extract_error(out: str) -> str:
    for ln in out.splitlines():
        if "Error while compiling" in ln or "FAILED:" in ln or "ParseException" in ln:
            return ln.strip()[:400]
    return out.strip()[:400]


def show_tables(dbname: str) -> list[str]:
    rows = run_sql(f"show tables in {dbname}")
    return [list(r.values())[0] for r in rows]


def describe(dbname: str, table: str) -> list[tuple[str, str, str]]:
    """返回 [(col_name, data_type, comment), ...]"""
    rows = run_sql(f"describe {dbname}.{table}")
    result = []
    for r in rows:
        vals = list(r.values())
        if len(vals) >= 2:
            col, typ = vals[0].strip(), vals[1].strip()
            comment = vals[2].strip() if len(vals) > 2 else ""
            if col.startswith("#") or not col or col in ("NULL",):
                break
            result.append((col, typ, comment))
    return result
