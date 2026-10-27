#!/usr/bin/env bash
# 把仓库自带的 git 钩子安装到 .git/hooks（WSL / Git Bash / macOS 用这个）
# 用法：bash tools/install-hooks.sh
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
SRC="$ROOT/tools/git-hooks"
DST="$ROOT/.git/hooks"

[ -d "$SRC" ] || { echo "找不到钩子源目录：$SRC" >&2; exit 1; }
mkdir -p "$DST"

for f in "$SRC"/*; do
  [ -f "$f" ] || continue
  cp "$f" "$DST/$(basename "$f")"
  chmod +x "$DST/$(basename "$f")"
  echo "  已安装 $(basename "$f")"
done

echo ""
echo "钩子安装完成，位置：$DST"
echo "自测：故意写一个不合规的提交信息，应被拒绝"
echo '  git commit --allow-empty -m "更新"'
