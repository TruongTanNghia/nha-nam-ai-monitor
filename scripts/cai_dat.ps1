# Cài đặt Nhà Nấm AI trên máy khách (Windows 10/11) — chạy qua CAI_DAT_NHA_NAM.bat
# Tự cài: Python, thư viện, Cloudflare Tunnel; tạo cấu hình; cho chạy ẩn cùng Windows; in link web.
param(
    [switch]$NoPrompt,            # không hỏi gì (dùng giá trị sẵn có / tham số)
    [string]$GeminiKey = "",
    [string]$ZaloToken = "",
    [string]$ZaloChatId = "",
    [ValidateSet("ask", "yes", "no")][string]$Autostart = "ask"
)
# "Continue": PowerShell 5.1 coi dòng cảnh báo (stderr) của pip/winget là lỗi -> tự kiểm tra $LASTEXITCODE
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$EnvFile = Join-Path $Root "server\.env"
$TaskName = "NhaNamAI"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    [OK] $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    [!] $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host "`n[LOI] $text" -ForegroundColor Red; exit 1 }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Ask($question, $default = "") {
    if ($NoPrompt) { return $default }
    $answer = Read-Host $question
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim()
}

# ---------- server\.env ----------
function Get-EnvValue($key) {
    if (-not (Test-Path $EnvFile)) { return "" }
    foreach ($line in [IO.File]::ReadAllLines($EnvFile)) {
        if ($line -match "^\s*$key=(.*)$") { return ($Matches[1] -replace "\s+#.*$", "").Trim() }
    }
    return ""
}
function Set-EnvValue($key, $value) {
    $lines = New-Object System.Collections.Generic.List[string]
    if (Test-Path $EnvFile) { $lines.AddRange([string[]][IO.File]::ReadAllLines($EnvFile)) }
    $done = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$key=") { $lines[$i] = "$key=$value"; $done = $true }
    }
    if (-not $done) { $lines.Add("$key=$value") }
    [IO.File]::WriteAllLines($EnvFile, $lines, (New-Object Text.UTF8Encoding($false)))
}

# ---------- Python ----------
function Find-Python {
    $probe = "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')"
    foreach ($cmd in @("py", "python")) {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { continue }
        try {
            if ($cmd -eq "py") { $exe = & py -3 -c $probe 2>$null } else { $exe = & python -c $probe 2>$null }
            if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path ($exe | Select-Object -First 1).Trim())) {
                return ($exe | Select-Object -First 1).Trim()
            }
        } catch { }
    }
    return $null
}

function Stop-NhaNam {
    $port = Get-EnvValue "PORT"; if (-not $port) { $port = "5000" }
    Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -match "^pythonw?\.exe$" -and $_.CommandLine -like "*$Root*server*app.py*") -or
        ($_.Name -eq "cloudflared.exe" -and $_.CommandLine -like "*localhost:$port*")
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Write-Host "=========================================" -ForegroundColor Green
Write-Host "   CAI DAT NHA NAM AI" -ForegroundColor Green
Write-Host "   $Root"
Write-Host "========================================="

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Fail "May chua co winget. Cai 'App Installer' tu Microsoft Store roi chay lai file nay."
}

Step "1/6 Python"
$py = Find-Python
if (-not $py) {
    Warn "Chua co Python 3.10+, dang cai Python 3.12..."
    winget install --id Python.Python.3.12 -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
    Refresh-Path
    $py = Find-Python
    if (-not $py) { Fail "Cai Python xong nhung chua tim thay. Dong cua so, mo lai CAI_DAT_NHA_NAM.bat." }
}
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
Ok $py

Step "2/6 Thu vien Python"
& $py -m pip install --disable-pip-version-check -q --upgrade pip *> $null
& $py -m pip install --disable-pip-version-check -q -r (Join-Path $Root "server\requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "Cai thu vien that bai - kiem tra Internet roi chay lai." }
Ok "flask, numpy, scikit-learn, ..."

Step "3/6 Cloudflare Tunnel (cloudflared)"
function Find-Cloudflared {
    $c = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @("${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe", "$env:ProgramFiles\cloudflared\cloudflared.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}
$cf = Find-Cloudflared
if (-not $cf) {
    winget install --id Cloudflare.cloudflared -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
    Refresh-Path
    $cf = Find-Cloudflared
    if (-not $cf) { Fail "Khong cai duoc cloudflared." }
}
Ok $cf

Step "4/6 Cau hinh (server\.env)"
if (-not (Test-Path $EnvFile)) { Copy-Item (Join-Path $Root "server\.env.example") $EnvFile; Ok "Tao server\.env tu file mau" }
Set-EnvValue "CLOUDFLARED_PATH" $cf
Set-EnvValue "TUNNEL_ENABLED" "true"
Set-EnvValue "LOG_FILE" "server/data/server.log"

$password = Get-EnvValue "APP_PASSWORD"
if (-not $password) {
    $chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    $password = "nhanam-" + (-join (1..10 | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] }))
    Set-EnvValue "APP_PASSWORD" $password
    Ok "Da tao mat khau dang nhap web"
}
if (-not (Get-EnvValue "APP_USER")) { Set-EnvValue "APP_USER" "admin" }

if (-not (Get-EnvValue "LLM_API_KEY")) {
    $key = if ($GeminiKey) { $GeminiKey } else { Ask "    Dan API key Gemini (Enter de bo qua)" }
    if ($key) {
        Set-EnvValue "LLM_API_KEY" $key
        Set-EnvValue "LLM_PROVIDER" "openai"
        Set-EnvValue "LLM_BASE_URL" "https://generativelanguage.googleapis.com/v1beta/openai"
        Set-EnvValue "LLM_MODEL" "gemini-3.5-flash-lite"
        Set-EnvValue "LLM_FALLBACK_MODELS" "gemini-3.8-flash,gemini-3.5-flash,gemini-3.1-flash-lite"
        Ok "Da luu key Gemini"
    }
}
if (-not (Get-EnvValue "ZALO_BOT_TOKEN")) {
    $token = if ($ZaloToken) { $ZaloToken } else { Ask "    Dan token Zalo Bot (Enter de bo qua)" }
    if ($token) { Set-EnvValue "ZALO_BOT_TOKEN" $token; Ok "Da luu token Zalo" }
}
if ((Get-EnvValue "ZALO_BOT_TOKEN") -and -not (Get-EnvValue "ZALO_CHAT_IDS")) {
    $chat = if ($ZaloChatId) { $ZaloChatId } else { Ask "    Chat ID Zalo (Enter de bo qua - nhan 'chatid' cho bot de lay sau)" }
    if ($chat) { Set-EnvValue "ZALO_CHAT_IDS" $chat; Ok "Da luu chat ID" }
}

Step "5/6 Tu chay cung Windows"
$auto = $Autostart
if ($auto -eq "ask") {
    if ((Ask "    Tu chay may chu khi bat may? (Y/n)" "y") -match "^[nN]") { $auto = "no" } else { $auto = "yes" }
}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
if ($auto -eq "yes") {
    try {
        $ErrorActionPreference = "Stop"  # trong khối này: lỗi đăng ký tác vụ -> nhảy vào catch
        $action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$Root\server\app.py`"" -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
        Ok "Da dang ky tac vu '$TaskName' (chay an moi khi dang nhap Windows)"
    } catch {
        Warn "Khong dang ky duoc tac vu tu chay ($($_.Exception.Message)). Chay file nay voi quyen Administrator."
        $auto = "no"
    } finally {
        $ErrorActionPreference = "Continue"
    }
} else {
    Warn "Khong tu chay - moi lan bat may hay chay lai file nay"
}

Step "6/6 Khoi dong may chu + mo duong ham Cloudflare"
Stop-NhaNam
$linkFile = Join-Path $Root "LINK_WEB.txt"
Remove-Item $linkFile -ErrorAction SilentlyContinue
if ($auto -eq "yes") { Start-ScheduledTask -TaskName $TaskName }
else { Start-Process -FilePath $pyw -ArgumentList "`"$Root\server\app.py`"" -WorkingDirectory $Root -WindowStyle Hidden }

$url = $null
for ($i = 0; $i -lt 45 -and -not $url; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path $linkFile) { $url = (Get-Content $linkFile -TotalCount 1 -Encoding UTF8).Trim() }
}
$port = Get-EnvValue "PORT"; if (-not $port) { $port = "5000" }

Write-Host "`n=========================================" -ForegroundColor Green
if ($url) { Write-Host " LINK WEB:   $url" -ForegroundColor Green }
else { Warn "Chua lay duoc link Cloudflare (mang cham?). Xem lai trong LINK_WEB.txt hoac tab Canh bao tren web." }
Write-Host " Trong may:  http://localhost:$port"
Write-Host " Tai khoan:  $(Get-EnvValue 'APP_USER')"
Write-Host " Mat khau:   $password"
Write-Host " Nhat ky:    $Root\server\data\server.log"
Write-Host " Tat:        DUNG_NHA_NAM.bat"
Write-Host "=========================================" -ForegroundColor Green
if ($url -and -not $NoPrompt) { Start-Process $url }
