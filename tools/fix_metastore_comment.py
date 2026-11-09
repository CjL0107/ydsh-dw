#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 Hive metastore 中文注释丢失为 '?' 的问题。

背景：
    metastore 库的 COLUMNS_V2.COMMENT / TABLE_PARAMS.PARAM_VALUE 原本是 latin1，
    Hive 写入中文时 MySQL 做 latin1 转换失败，被替换成 '?'（0x3F），原始字节已不可恢复。
    列字符集已改为 utf8mb4（新表写入正常），但历史表的注释需要按 DDL 脚本回填。

做法：
    1) 扫描 sql/**/*.sql，解析 CREATE TABLE 的字段注释与表注释；
    2) 生成 UPDATE 语句，按 (库名, 表名, 字段序号) 命中 COLUMNS_V2，
       按 (表名) 命中 TABLE_PARAMS 中 PARAM_KEY='comment' 的行；
    3) 输出 fix_metastore_comment.sql，用 utf8mb4 连接在 mysql 里执行。

用法：
    python tools/fix_metastore_comment.py            # 生成 SQL
    python tools/fix_metastore_comment.py --report   # 只打印解析结果，不生成
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
OUT_FILE = ROOT / ".tmp" / "w3" / "fix_metastore_comment.sql"

# CREATE TABLE db.tbl ( ... ) COMMENT '表注释'
RE_CREATE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*\(",
    re.IGNORECASE,
)
# 字段行： name type ... COMMENT 'xxx'
RE_COL_COMMENT = re.compile(
    r"^\s*([A-Za-z_]\w*)\s+[\w<>(),\s]+?\s+COMMENT\s+'((?:[^']|'')*)'",
    re.IGNORECASE,
)
RE_TBL_COMMENT = re.compile(r"\)\s*COMMENT\s+'((?:[^']|'')*)'", re.IGNORECASE)
# CREATE DATABASE IF NOT EXISTS db COMMENT '库注释'
RE_CREATE_DB = re.compile(
    r"CREATE\s+DATABASE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_]\w*)\s+COMMENT\s+'((?:[^']|'')*)'",
    re.IGNORECASE,
)
# 分区字段 PARTITIONED BY ( dt STRING COMMENT '...' )
RE_PART = re.compile(r"PARTITIONED\s+BY\s*\((.*?)\)", re.IGNORECASE | re.DOTALL)


def split_blocks(text):
    """按 CREATE TABLE 切块，正确处理嵌套括号。"""
    for m in RE_CREATE.finditer(text):
        db, tbl = m.group(1), m.group(2)
        i = m.end()  # 指向 '(' 之后
        depth = 1
        buf = []
        while i < len(text) and depth > 0:
            ch = text[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            buf.append(ch)
            i += 1
        body = "".join(buf)
        # 括号后到分号之间还有 COMMENT '...'
        tail_end = text.find(";", i)
        tail = text[i : tail_end if tail_end != -1 else len(text)]
        yield db.lower(), tbl.lower(), body, tail


def parse_file(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    out = {}
    db_comments = {m.group(1).lower(): m.group(2).replace("''", "'") for m in RE_CREATE_DB.finditer(text)}
    for db, tbl, body, tail in split_blocks(text):
        cols = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            m = RE_COL_COMMENT.match(line)
            if m:
                cols.append((m.group(1).lower(), m.group(2).replace("''", "'")))
        # 分区字段另存：注释在 PARTITION_KEYS.PKEY_COMMENT，不在 COLUMNS_V2
        part_cols = []
        pm = RE_PART.search(tail)
        if pm:
            for line in pm.group(1).splitlines():
                m = RE_COL_COMMENT.match(line.strip())
                if m:
                    part_cols.append((m.group(1).lower(), m.group(2).replace("''", "'")))
        tm = RE_TBL_COMMENT.search(tail)
        tbl_comment = tm.group(1).replace("''", "'") if tm else None
        key = (db, tbl)
        # 同表多次出现时以最后一次为准
        out[key] = {
            "cols": cols,
            "part_cols": part_cols,
            "comment": tbl_comment,
            "file": path.name,
        }
    return out, db_comments


def esc(s):
    return s.replace("\\", "\\\\").replace("'", "''")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    tables, dbs = {}, {}
    for f in sorted(SQL_DIR.rglob("*.sql")):
        t, d = parse_file(f)
        tables.update(t)
        dbs.update(d)

    if not tables:
        print("未解析到任何 CREATE TABLE，检查 sql/ 目录")
        return 1

    total_cols = sum(len(v["cols"]) for v in tables.values())
    print(f"解析到 {len(tables)} 张表 / {total_cols} 个字段注释")

    if args.report:
        for (db, tbl), v in sorted(tables.items()):
            print(f"  {db}.{tbl:28s} cols={len(v['cols']):2d}  table_comment={v['comment']}")
        return 0

    lines = [
        "-- 自动生成：回填 metastore 中文注释（列字符集已改为 utf8mb4）",
        "SET NAMES utf8mb4;",
        "USE metastore;",
        "",
    ]
    for db, cmt in sorted(dbs.items()):
        lines.append("UPDATE DBS SET `DESC`='%s' WHERE NAME='%s';" % (esc(cmt), db))
    lines.append("")
    for (db, tbl), v in sorted(tables.items()):
        tgt = f"(SELECT t.TBL_ID FROM TBLS t JOIN DBS d ON t.DB_ID=d.DB_ID WHERE d.NAME='{db}' AND t.TBL_NAME='{tbl}')"
        for idx, (col, cmt) in enumerate(v["cols"]):
            lines.append(
                "UPDATE COLUMNS_V2 SET COMMENT='%s' "
                "WHERE CD_ID=(SELECT s.CD_ID FROM TBLS t "
                "JOIN DBS d ON t.DB_ID=d.DB_ID JOIN SDS s ON t.SD_ID=s.SD_ID "
                "WHERE d.NAME='%s' AND t.TBL_NAME='%s') "
                "AND COLUMN_NAME='%s' AND INTEGER_IDX=%d;" % (esc(cmt), db, tbl, col, idx)
            )
        if v["comment"]:
            lines.append(
                "UPDATE TABLE_PARAMS SET PARAM_VALUE='%s' WHERE TBL_ID=%s AND PARAM_KEY='comment';"
                % (esc(v["comment"]), tgt)
            )
        for pidx, (pcol, pcmt) in enumerate(v.get("part_cols", [])):
            lines.append(
                "UPDATE PARTITION_KEYS SET PKEY_COMMENT='%s' WHERE TBL_ID=%s "
                "AND PKEY_NAME='%s' AND INTEGER_IDX=%d;" % (esc(pcmt), tgt, pcol, pidx)
            )
        lines.append("")

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成 {OUT_FILE}  ({len(lines)} 行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
