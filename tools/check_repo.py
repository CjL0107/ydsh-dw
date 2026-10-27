# -*- coding: utf-8 -*-
"""Git 规范自检：输出一张对应「Git 规范 20 分」评分点的检查表。

用法：
    python tools/check_repo.py

只读，不会修改仓库任何内容。
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

TYPES = r"feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|merge"
COMMIT_RE = re.compile(rf"^({TYPES})(\([A-Za-z0-9/_-]+\))?: .+")

# 必须被 .gitignore 拦住的路径（命中即视为配置缺失）
IGNORE_RULES = ["*.csv", ".env", "*.log", "__pycache__"]

# 绝对不能出现在 git ls-files 里的后缀/文件名
FORBIDDEN = re.compile(r"(\.csv|\.tsv|\.log|\.env)$|^\.env$", re.I)


def git(*args: str) -> str:
    """执行 git 命令并返回 stdout，失败返回空串。"""
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return out.stdout.strip()
    except FileNotFoundError:
        print("找不到 git 命令，请确认 PATH 中已安装 Git")
        sys.exit(1)


def main() -> int:
    root = git("rev-parse", "--show-toplevel")
    if not root:
        print("当前目录不是 git 仓库")
        return 1

    results: list[tuple[str, str, str]] = []  # (状态, 检查项, 说明)

    # ---------- 1. 提交信息合规率 ----------
    subjects = [s for s in git("log", "--format=%s").splitlines() if s.strip()]
    exempt = [s for s in subjects if s.startswith("Merge ")]
    checked = [s for s in subjects if not s.startswith("Merge ")]
    bad = [s for s in checked if not COMMIT_RE.match(s)]
    ok = len(checked) - len(bad)
    rate = (ok / len(checked) * 100) if checked else 0

    if checked and not bad:
        results.append(("通过", "提交信息规范",
                        f"{ok}/{len(checked)} 条合规（100%），另有 {len(exempt)} 条合并提交不计"))
    elif checked:
        sample = "；".join(bad[:3])
        results.append(("未通过", "提交信息规范",
                        f"{ok}/{len(checked)} 条合规（{rate:.0f}%），不合规样例：{sample}"))
    else:
        results.append(("未通过", "提交信息规范", "仓库还没有任何提交"))

    # ---------- 2. 提交类型分布 ----------
    dist = Counter()
    for s in checked:
        m = COMMIT_RE.match(s)
        if m:
            dist[m.group(1)] += 1
    if dist:
        detail = " ".join(f"{k}:{v}" for k, v in dist.most_common())
        results.append(("通过", "提交类型覆盖", detail))
    else:
        results.append(("未通过", "提交类型覆盖", "没有符合规范的提交，无法统计"))

    # ---------- 3. 敏感文件是否被跟踪 ----------
    tracked = [f for f in git("ls-files").splitlines() if FORBIDDEN.search(f)]
    if tracked:
        results.append(("未通过", "敏感文件未入库",
                        f"发现 {len(tracked)} 个不该跟踪的文件：{', '.join(tracked[:3])}"))
    else:
        results.append(("通过", "敏感文件未入库", "CSV / .env / 日志均未被跟踪"))

    # ---------- 4. .gitignore 关键规则 ----------
    gi_path = Path(root) / ".gitignore"
    if not gi_path.exists():
        results.append(("未通过", ".gitignore 完整", "文件不存在"))
    else:
        gi = gi_path.read_text(encoding="utf-8", errors="replace")
        missing = [r for r in IGNORE_RULES if r not in gi]
        if missing:
            results.append(("未通过", ".gitignore 完整", f"缺少规则：{', '.join(missing)}"))
        else:
            results.append(("通过", ".gitignore 完整", "数据 / 密钥 / 日志 / 缓存均有覆盖"))

    # ---------- 5. 主干分支命名 ----------
    branches = git("branch", "--format=%(refname:short)").splitlines()
    current = git("branch", "--show-current")
    if "main" in branches:
        tail = "，当前在 main" if current == "main" else f"，当前在 {current}（分支开发中属正常）"
        results.append(("通过", "主干分支为 main", f"存在 main{tail}"))
    else:
        results.append(("未通过", "主干分支为 main",
                        f"未找到 main，现有分支：{', '.join(branches) or '无'}（手册要求 git branch -M main）"))

    # ---------- 6. 分支使用情况 ----------
    others = [b for b in branches if b not in ("main",)]
    has_merge = bool(exempt)
    if has_merge:
        results.append(("通过", "使用了分支与合并", f"历史中有 {len(exempt)} 个合并节点，分支痕迹可追溯"))
    elif others:
        results.append(("提示", "使用了分支与合并", f"存在其他分支（{', '.join(others[:3])}），但还没有合并记录"))
    else:
        results.append(("提示", "使用了分支与合并", "只有 main 一条分支，建议按 docs/Git协作规范.md 用 feature/* 开发"))

    # ---------- 7. 作者分布（每人都有提交记录）----------
    authors = Counter(a for a in git("log", "--format=%an <%ae>").splitlines() if a.strip())
    if len(authors) >= 3:
        results.append(("通过", "团队成员均有提交", f"{len(authors)} 位作者：" + "；".join(
            f"{n} ({c} 次)" for n, c in authors.most_common())))
    elif len(authors) == 2:
        results.append(("提示", "团队成员均有提交",
                        f"目前 2 位作者：" + "；".join(f"{n} ({c} 次)" for n, c in authors.most_common())
                        + "。评分要求 3～4 人都有记录，其余成员需各自配置 user.name/user.email 后提交"))
    else:
        results.append(("提示", "团队成员均有提交",
                        f"目前仅 {len(authors)} 位作者：" + "；".join(authors) +
                        "。每位成员必须配置自己的 user.name / user.email（与 GitHub 账号邮箱一致）"))

    # ---------- 8. 远程仓库 ----------
    remotes = git("remote", "-v").splitlines()
    if any("origin" in r for r in remotes):
        url = remotes[0].split()[1] if len(remotes[0].split()) > 1 else "?"
        results.append(("通过", "已配置远程仓库", f"origin -> {url}"))
    else:
        results.append(("未通过", "已配置远程仓库",
                        "未配置 origin。子任务 3 要求完成首次 push，见文档第二节"))

    # ---------- 9. .gitattributes 与工作区 ----------
    ga = Path(root) / ".gitattributes"
    if ga.exists():
        results.append(("通过", ".gitattributes 存在", "已统一换行符（shell 脚本强制 LF）"))
    else:
        results.append(("提示", ".gitattributes 存在", "缺失，Windows 与 WSL 混用时 shell 脚本可能带 CRLF 报错"))

    dirty = git("status", "--porcelain")
    if dirty:
        n = len(dirty.splitlines())
        results.append(("提示", "工作区干净", f"有 {n} 个未提交改动"))
    else:
        results.append(("通过", "工作区干净", "没有未提交改动"))

    # ---------- 输出 ----------
    width = max(len(r[1]) for r in results) + 2
    print("=" * 76)
    print("  Git 规范自检（对应评分项：Git 规范 20 分）")
    print("=" * 76)
    for status, item, detail in results:
        mark = {"通过": "[通过]", "未通过": "[未过]", "提示": "[提示]"}[status]
        print(f"{mark} {item.ljust(width - 6)} {detail}")
    print("-" * 76)

    passed = sum(1 for r in results if r[0] == "通过")
    failed = sum(1 for r in results if r[0] == "未通过")
    hints = sum(1 for r in results if r[0] == "提示")
    print(f"  通过 {passed} 项 / 未通过 {failed} 项 / 提示 {hints} 项")
    if failed:
        print("  未通过项按 docs/Git协作规范.md 处理后可再次运行本脚本")
    else:
        print("  无未通过项，可用于答辩演示")
    print("=" * 76)

    print("\n附：分支与合并历史")
    graph = git("log", "--graph", "--oneline", "--all", "--decorate", "-12")
    print(graph or "（无提交）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
