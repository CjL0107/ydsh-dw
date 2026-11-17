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
import os
import re
import subprocess
import sys
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


def run_sql(sql: str, timeout: int = 600, max_rows: int | None = None,
            sep: str = "csv") -> list[dict[str, Any]]:
    """执行 SQL。

    两种后端，由环境变量 HIVE_BACKEND 决定：
      · docker（默认）—— 宿主机上 `docker exec beeline`，开发期用，好处是能直接
        复用容器里的 beeline，不用在本机装 Hive 客户端
      · pyhive —— 走 Thrift 直连 hive-server:10000，容器内运行时用
        （容器里没有 docker 命令，exec 那条路走不通）
    两条路的返回值结构一致：list[dict]，值统一为字符串，避免下游类型判断分叉。
    """
    """执行单条 SQL，返回 list[dict]（表头做 key）

    max_rows：默认走 MAX_ROWS 安全上限；批处理脚本（如全量日序列）可显式放宽，
    但仍需调用方自己保证 SQL 有 LIMIT 或聚合，避免全表扫描拖死容器。

    sep：'csv'（默认）或 'tsv'。元数据查询（describe）必须用 tsv——
    Hive 的 decimal(16,2) 类型名里带逗号，走 CSV 会被多切一刀导致整行对不齐
    而被丢弃，最终所有 decimal 列都不会出现在 schema 里，模型自然查不到
    gmv / gmv_share / aov 这些关键指标列。
    """
    limit_rows = max_rows or MAX_ROWS
    s = _normalize(sql)

    if os.getenv("HIVE_BACKEND", "docker").lower() == "pyhive":
        return _run_pyhive(s, timeout=timeout, max_rows=limit_rows)

    fmt = "tsv2" if sep == "tsv" else "csv2"
    delimiter = "\t" if sep == "tsv" else ","
    cmd = [
        "docker", "exec", "-i", CONTAINER,
        "beeline", "-u", HIVE_URL, "-n", "root",
        "--silent=true", f"--outputformat={fmt}", "-f", "/dev/stdin",
    ]
    proc = subprocess.run(
        cmd, input=s, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""

    # 容器不可用时 docker exec 会把错误写到 stderr、stdout 为空。
    # 若不在这里拦住，run_sql 会返回 []，调用方会把"查不到数据"当成"结果为空"，
    # 两个空结果一比对就成了"一致"——验证脚本会给出完全错误的满分。
    if proc.returncode != 0:
        raise RuntimeError(f"beeline 执行失败（rc={proc.returncode}）：{_clean_noise(err or out)[:300]}")
    if "Error response from daemon" in err or "is not running" in err:
        raise RuntimeError(f"容器不可用：{err.strip()[:300]}")

    if "Error while compiling" in out or "FAILED:" in out or "ParseException" in out:
        raise RuntimeError(_extract_error(out))

    lines = [ln for ln in out.splitlines() if ln.strip()]
    lines = [ln for ln in lines
             if not ln.startswith(("SLF4J", "log4j", "[WARN]", "No such file",
                                   "INFO", "WARN", "Beeline", "Closing",
                                   "Connected to", "Transaction isolation",
                                   "Hive Session ID", "Cannot connect"))]
    if not lines:
        return []

    header = next(csv.reader([lines[0]], delimiter=delimiter))
    # beeline 对 `select *` 返回的是表名限定的列名：ads_store_top10.gmv。
    # 直接拿它当 dict key，下游按裸列名取值就会全部取不到 —— 第 4 周把 ADS
    # 同步到 MySQL 时，10 行数据全写成 NULL 就是这么来的（行数对、值全空，
    # 比直接报错更难发现）。这里统一剥掉"表名."前缀，前缀不一致时不动。
    if header and all("." in h for h in header):
        prefixes = {h.rsplit(".", 1)[0] for h in header}
        if len(prefixes) == 1:
            header = [h.split(".", 1)[1] for h in header]

    rows: list[dict[str, Any]] = []
    bad = 0
    for ln in lines[1:]:
        try:
            vals = next(csv.reader([ln], delimiter=delimiter))
        except Exception:
            bad += 1
            continue
        if len(vals) == len(header):
            rows.append(dict(zip(header, vals)))
        else:
            bad += 1
        if len(rows) >= limit_rows:
            break
    # 有数据行却一行都对齐不上，说明表头取错了（多半是日志行混进了首行）。
    # 这种情况必须报错，否则会静默返回空结果，被当成"真没数据"。
    if not rows and bad:
        raise RuntimeError(f"结果解析失败：{bad} 行无法与表头对齐，疑似日志混入。表头={header[:6]}")
    # 部分行对不齐同样危险：之前所有 decimal 列就是这样被静默吃掉的
    # （decimal(16,2) 里的逗号在 CSV 下被当成分隔符），schema 少了一半指标列
    # 却毫无提示，模型只能用猜的。这里至少要在 stderr 留痕。
    if bad:
        print(f"[hive_runner] 警告：{bad} 行与表头（{len(header)} 列）不匹配已丢弃，"
              f"SQL={s[:80]}", file=sys.stderr)
    return rows


def _run_pyhive(sql: str, timeout: int = 600, max_rows: int = MAX_ROWS) -> list[dict[str, Any]]:
    """Thrift 直连 HiveServer2（容器内运行用）"""
    try:
        from pyhive import hive  # 延迟导入：宿主机开发时不需要装
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("未安装 pyhive，容器镜像里需要 pip install pyhive thrift") from e

    conn = hive.Connection(
        host=os.getenv("HIVE_HOST", "hive-server"),
        port=int(os.getenv("HIVE_PORT", "10000")),
        username=os.getenv("HIVE_USER", "hive"),
        database=os.getenv("HIVE_DATABASE", "default"),
        auth=os.getenv("HIVE_AUTH", "NONE"),
    )
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [d[0].split(".")[-1] for d in cur.description]
        rows: list[dict[str, Any]] = []
        for rec in cur:
            rows.append({c: ("" if v is None else str(v)) for c, v in zip(cols, rec)})
            if len(rows) >= max_rows:
                break
        return rows
    finally:
        conn.close()


def _extract_error(out: str) -> str:
    for ln in out.splitlines():
        if "Error while compiling" in ln or "FAILED:" in ln or "ParseException" in ln:
            return ln.strip()[:400]
    return _clean_noise(out)[:400]


def _clean_noise(text: str) -> str:
    """去掉 beeline 的启动噪声（SLF4J 冲突、WARN、目录不存在等），只留真正的报错。

    不清理的话报错信息前 300 字符会被 SLF4J 的 binding 提示占满，
    调用方看到的永远是同一段无关内容，真正的失败原因被截断丢掉。
    """
    noise_prefix = ("SLF4J", "log4j", "[WARN]", "No such file", "WARN ", "INFO ",
                    "Beeline version", "Transaction isolation", "Hive Session ID")
    kept = [ln for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith(noise_prefix)]
    return "\n".join(kept).strip()


def show_tables(dbname: str) -> list[str]:
    rows = run_sql(f"show tables in {dbname}")
    return [list(r.values())[0] for r in rows]


def describe(dbname: str, table: str) -> list[tuple[str, str, str]]:
    """返回 [(col_name, data_type, comment), ...]

    必须走 tsv：类型名 decimal(16,2) 里的逗号会把 CSV 行切乱。
    """
    rows = run_sql(f"describe {dbname}.{table}", sep="tsv")
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
