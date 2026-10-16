# Tắt máy chủ Nhà Nấm AI và đường hầm Cloudflare. Thêm -GoTuChay để bỏ chạy cùng Windows.
param([switch]$GoTuChay)
$Root = Split-Path -Parent $PSScriptRoot
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match "^pythonw?\.exe$" -and $_.CommandLine -like "*$Root*server*app.py*") -or
    ($_.Name -eq "cloudflared.exe" -and $_.CommandLine -like "*tunnel*--url http://localhost:*")
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Da tat: $($_.Name) ($($_.ProcessId))"
}
if ($GoTuChay) {
    Unregister-ScheduledTask -TaskName "NhaNamAI" -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Da go che do tu chay cung Windows"
}
