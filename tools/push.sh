#!/usr/bin/env bash
# 自动探测本机可用代理并推送到 GitHub。
# 用法：bash tools/push.sh [remote] [branch]
#
# 背景：本机到 github.com:443 的直连经常被阻断，而代理端口（Clash/V2Ray 等）
# 每次重启都会变。本脚本先扫描本机监听端口，找出真正能穿透到 GitHub 的
# HTTP 代理，再用它完成 git push，避免每次手工 netstat + 试端口。

set -uo pipefail

REMOTE="${1:-origin}"
BRANCH="${2:-main}"
PROBE_URL="https://api.github.com/zen"
CANDIDATES=""

cd "$(dirname "$0")/.." || exit 1

echo "==> 1/3 收集本机监听端口"
CANDIDATES=$(netstat -ano 2>/dev/null \
  | grep -i "LISTENING" \
  | grep "127.0.0.1:" \
  | awk '{print $2}' | sed 's/.*://' \
  | sort -un)

if [ -z "$CANDIDATES" ]; then
  echo "!! 没有发现任何本机监听端口，无法走代理。"
  echo "   如果代理是全局 TUN 模式，试试直接：git push $REMOTE $BRANCH"
  exit 2
fi

echo "    候选端口：$(echo "$CANDIDATES" | tr '\n' ' ')"

echo "==> 2/3 逐个探测能否穿透 GitHub"
WORKING=""
for p in $CANDIDATES; do
  out=$(curl -s -m 4 -x "http://127.0.0.1:$p" "$PROBE_URL" 2>/dev/null)
  if [ -n "$out" ] && [ "${out:0:1}" != "<" ]; then
    echo "    ✔ 127.0.0.1:$p  ->  $out"
    WORKING="$p"
    break
  fi
done

if [ -z "$WORKING" ]; then
  echo "!! 所有端口都没能穿透 GitHub。"
  echo "   可能原因：代理未启动 / 节点未选中 / 当前网络完全断开。"
  echo "   建议：打开代理客户端并选一个可用节点，然后重跑本脚本。"
  echo "   备用通道：github.com:22（SSH）本机通常是通的，可用 SSH 方式推送。"
  exit 3
fi

echo "==> 3/3 使用 127.0.0.1:$WORKING 推送 $REMOTE/$BRANCH"
if git -c "http.proxy=http://127.0.0.1:$WORKING" \
       -c "https.proxy=http://127.0.0.1:$WORKING" \
       push "$REMOTE" "$BRANCH"; then
  echo
  echo "==> 推送成功。远端 HEAD："
  git -c "http.proxy=http://127.0.0.1:$WORKING" ls-remote "$REMOTE" "refs/heads/$BRANCH" \
    | awk '{print "    "$0}'
  exit 0
else
  echo
  echo "!! git push 失败（代理已通，但推送环节出错）。"
  echo "   若提示认证失败，检查 Windows 凭据管理器中的 github.com 凭据；"
  echo "   若提示 non-fast-forward，先 git pull --rebase 后再推送。"
  exit 4
fi
