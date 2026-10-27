#!/usr/bin/env bash
# 一键执行第 1 周全流程（Git Bash / WSL / macOS / Linux）
# 用法：在项目根目录执行  bash scripts/run_all.sh
set -euo pipefail

# Git Bash（MSYS）会把 /sql/xxx.sql 这类以斜杠开头的参数自动改写成
# C:/Program Files/Git/sql/xxx.sql，导致容器里报
#   No FileSystem for scheme "C"
# 关掉这个转换。
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

run_sql() {
  local path="$1" label="$2"
  echo ""
  echo "=============================================="
  echo "  $label"
  echo "  -> $path"
  echo "=============================================="
  docker exec -i hive-server beeline -u 'jdbc:hive2://localhost:10000/default' -f "$path"
}

echo "[0/6] 检查 Docker 与数据文件..."
if ! docker ps >/dev/null 2>&1; then
  echo "Docker 未运行，请先启动 Docker Desktop" >&2
  exit 1
fi
n=$(ls data/*.csv 2>/dev/null | wc -l | tr -d ' ')
if [ "$n" -lt 6 ]; then
  echo "data/ 目录下只有 $n 个 CSV，需要 6 个" >&2
  exit 1
fi

echo "[1/6] 等待 HiveServer2 就绪（最长 3 分钟）..."
ready=0
for i in $(seq 1 60); do
  if docker exec hive-server bash -c "echo > /dev/tcp/127.0.0.1/10000" >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 3
done
if [ "$ready" -ne 1 ]; then
  echo "HiveServer2 未就绪，请执行 docker logs hive-server 查看原因" >&2
  exit 1
fi
echo "  HiveServer2 已就绪"

run_sql "/sql/ods/create_ods.sql"         "步骤 1/4  创建 ODS 六张表"
run_sql "/sql/ods/load_ods.sql"           "步骤 2/4  导入 CSV 并校验行数"
run_sql "/sql/quality/quality_assess.sql" "步骤 3/4  五维度数据质量预评估"
run_sql "/sql/clean/clean_user.sql"       "步骤 4/4A 用户表缺失值处理"
run_sql "/sql/clean/clean_product.sql"    "步骤 4/4B 商品表缺失值处理"
run_sql "/sql/clean/clean_order.sql"      "步骤 4/4C 订单表去重"

echo ""
echo "全部执行完成。"
echo "请核对：dwd_order 应为 15000 行，dwd_user 年龄异常应为 0。"
