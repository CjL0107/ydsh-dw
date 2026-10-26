# 一键执行第 1 周全流程（Windows PowerShell）
# 用法：在项目根目录执行  .\scripts\run_all.ps1
# 说明：PowerShell 5.1 兼容写法，不使用 && 与三元运算符
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Invoke-Sql($sqlPath, $label) {
    Write-Host ""
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host "  $label" -ForegroundColor Cyan
    Write-Host "  -> $sqlPath" -ForegroundColor DarkGray
    Write-Host "==============================================" -ForegroundColor Cyan
    docker exec -i hive-server beeline -u 'jdbc:hive2://localhost:10000/default' -f $sqlPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[失败] $label 执行出错，请检查上方日志" -ForegroundColor Red
        exit 1
    }
}

# ---------- 0) 前置检查 ----------
Write-Host "[0/6] 检查 Docker 与数据文件..." -ForegroundColor Yellow
docker ps --format "{{.Names}}" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker 未运行，请先启动 Docker Desktop" -ForegroundColor Red
    exit 1
}

$csvCount = (Get-ChildItem -Path (Join-Path $Root "data") -Filter "*.csv" -ErrorAction SilentlyContinue).Count
if ($csvCount -lt 6) {
    Write-Host "data/ 目录下只有 $csvCount 个 CSV，需要 6 个" -ForegroundColor Red
    exit 1
}

# ---------- 1) 等待 HiveServer2 就绪 ----------
Write-Host "[1/6] 等待 HiveServer2 就绪（最长 3 分钟）..." -ForegroundColor Yellow
$ready = $false
for ($i = 1; $i -le 60; $i++) {
    docker exec hive-server bash -c "echo > /dev/tcp/127.0.0.1/10000" 2>$null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $ready) {
    Write-Host "HiveServer2 未就绪，请执行 docker logs hive-server 查看原因" -ForegroundColor Red
    exit 1
}
Write-Host "  HiveServer2 已就绪" -ForegroundColor Green

# ---------- 2~5) 依次执行 SQL ----------
Invoke-Sql "/sql/ods/create_ods.sql"            "步骤 1/4  创建 ODS 六张表"
Invoke-Sql "/sql/ods/load_ods.sql"              "步骤 2/4  导入 CSV 并校验行数"
Invoke-Sql "/sql/quality/quality_assess.sql"    "步骤 3/4  五维度数据质量预评估"
Invoke-Sql "/sql/clean/clean_user.sql"          "步骤 4/4A 用户表缺失值处理"
Invoke-Sql "/sql/clean/clean_product.sql"       "步骤 4/4B 商品表缺失值处理"
Invoke-Sql "/sql/clean/clean_order.sql"         "步骤 4/4C 订单表去重"

Write-Host ""
Write-Host "全部执行完成。" -ForegroundColor Green
Write-Host "请核对：dwd_order 应为 15000 行，dwd_user 年龄异常应为 0。" -ForegroundColor Green
