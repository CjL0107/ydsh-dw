# 把仓库自带的 git 钩子安装到 .git/hooks（每人在自己机器上跑一次）
# 用法：.\tools\install-hooks.ps1

$ErrorActionPreference = "Stop"

$root = (git rev-parse --show-toplevel 2>$null)
if (-not $root) {
    Write-Host "当前目录不是 git 仓库，请先 cd 到项目根目录" -ForegroundColor Red
    exit 1
}

$src = Join-Path $root "tools\git-hooks"
$dst = Join-Path $root ".git\hooks"

if (-not (Test-Path $src)) {
    Write-Host "找不到钩子源目录：$src" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $dst)) {
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
}

Get-ChildItem $src -File | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $dst $_.Name) -Force
    Write-Host "  已安装 $($_.Name)" -ForegroundColor Green
}

Write-Host ""
Write-Host "钩子安装完成，位置：$dst" -ForegroundColor Cyan
Write-Host "自测：故意写一个不合规的提交信息，应被拒绝"
Write-Host '  git commit --allow-empty -m "更新"' -ForegroundColor DarkGray
Write-Host "应在输出中看到「提交被拒绝」" -ForegroundColor DarkGray
