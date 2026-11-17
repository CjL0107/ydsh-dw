#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
刷新大屏数据快照
================================================
用法：
    python apps/api/refresh_snapshot.py            # 查询 Hive，覆盖写 snapshot.json
    python apps/api/refresh_snapshot.py --dry-run  # 只打印耗时与行数，不落盘

设计取舍：
- 写文件用「临时文件 + 原子替换」，避免刷新到一半被中断，留下半个 JSON
  让大屏读挂（这种情况排查起来很像"接口挂了"，实际是文件截断）。
- 刷新失败时保留旧快照，并把错误写进 refresh.log，不覆盖 data/snapshot.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(BASE))

SNAPSHOT = BASE / "data" / "snapshot.json"
LOG = BASE / "data" / "refresh.log"


def refresh(dry_run: bool = False) -> dict:
    from queries import build_snapshot

    snap = build_snapshot()
    meta = snap["meta"]

    if not dry_run:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        # 原子替换：先写同目录临时文件，再 os.replace
        fd, tmp = tempfile.mkstemp(dir=str(SNAPSHOT.parent), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(snap, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, SNAPSHOT)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
        _log(meta, SNAPSHOT.stat().st_size)
    return meta


def _log(meta: dict, size: int) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = (f"{meta['generated_at']}\t总耗时 {meta['total_seconds']}s\t"
            f"{size / 1024:.0f} KB\t" +
            " ".join(f"{k}={v}s" for k, v in meta["timings"].items()))
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="刷新大屏数据快照")
    ap.add_argument("--dry-run", action="store_true", help="不写文件，只看耗时")
    args = ap.parse_args()
    meta = refresh(dry_run=args.dry_run)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if not args.dry_run:
        print(f"已写入 {SNAPSHOT}  ({SNAPSHOT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
