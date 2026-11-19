# Dựng toàn bộ pipeline từ máy trống (cần: git, uv, ffmpeg trong PATH).
#   .\scripts\bootstrap.ps1            # venv + clone repo + cài social-auto-upload
#   .\scripts\bootstrap.ps1 -Browsers  # thêm: tải trình duyệt Chromium cho `sau` (~150 MB, cần khi đăng nhập/đăng bài)
#   .\scripts\bootstrap.ps1 -All       # clone cả repo tuỳ chọn trong vendor.toml
param([switch]$Browsers, [switch]$All)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

foreach ($tool in "git", "uv", "ffmpeg") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Thiếu '$tool' trong PATH." }
}

Write-Host "== 1/4 venv điều phối (yt-dlp + vnc) =="
if (-not (Test-Path .venv\Scripts\python.exe)) { uv venv .venv --python 3.12 }
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"

Write-Host "== 2/4 clone repo theo vendor.toml (ghim commit) =="
if ($All) { .venv\Scripts\python scripts\vendor.py --all } else { .venv\Scripts\python scripts\vendor.py }

Write-Host "== 3/4 social-auto-upload (venv riêng, theo uv.lock của họ) =="
$sau = "vendor\social-auto-upload"
if (Test-Path $sau) {
    if (-not (Test-Path "$sau\conf.py") -and (Test-Path "$sau\conf.example.py")) { Copy-Item "$sau\conf.example.py" "$sau\conf.py" }
    uv sync --project $sau --python 3.12
    if ($Browsers) { & "$sau\.venv\Scripts\python.exe" -m patchright install chromium }
    else { Write-Host "   (bỏ qua tải Chromium — chạy lại với -Browsers khi cần đăng nhập/đăng bài)" }
}

Write-Host "== 4/4 cấu hình =="
if (-not (Test-Path config.toml)) { Copy-Item config.example.toml config.toml; Write-Host "   đã tạo config.toml — kiểm tra [tools] và [paths]" }

Write-Host ""
.venv\Scripts\vnc doctor
