# 一键推送到 GitHub（本机直连 github.com 不通，需要本地代理）
param(
    [string]$Proxy = 'http://127.0.0.1:7897'
)

$env:HTTPS_PROXY = $Proxy
$env:HTTP_PROXY = $Proxy

Set-Location $PSScriptRoot
git add -A
git status --short
git commit -m ($args -join ' ')
git push
