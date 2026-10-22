# Tắt máy chủ Nhà Nấm AI + đường hầm Cloudflare còn chạy ngầm, và bỏ tự chạy cùng Windows (bản cài cũ).
# Chỉ tắt máy chủ Nhà Nấm (ở bất kỳ thư mục nào, kể cả bản cũ chạy ngầm), không đụng chương trình khác
function Test-NhaNamProcess($p) {
    if ($p.Name -eq "cloudflared.exe") { return $p.CommandLine -like "*tunnel*--url http://localhost:*" }
    if ($p.Name -notmatch "^pythonw?\.exe$" -or $p.CommandLine -notmatch '([A-Za-z]:[\\/][^"]*?server[\\/]app\.py)') { return $false }
    $app = $Matches[1]
    if (-not [IO.Path]::IsPathRooted($app)) { return $false }
    return Test-Path (Join-Path (Split-Path $app) "commands.py")
}

Get-CimInstance Win32_Process | Where-Object { Test-NhaNamProcess $_ } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Da tat: $($_.Name) ($($_.ProcessId))"
}
if (Get-ScheduledTask -TaskName "NhaNamAI" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "NhaNamAI" -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Da bo che do tu chay cung Windows"
}
Write-Host "Xong."
