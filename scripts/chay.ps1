# Chạy Nhà Nấm AI: 1 lần bấm (CHAY_NHA_NAM.bat) là chạy web + máy chủ AI + đường hầm Cloudflare (link từ xa).
# Lần đầu tự cài Python, thư viện, cloudflared. Đóng cửa sổ = tắt hết. KHÔNG tự chạy cùng Windows.
param([switch]$NoBrowser)
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

function Find-Python {
    $probe = "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')"
    foreach ($cmd in @("py", "python")) {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { continue }
        try {
            if ($cmd -eq "py") { $exe = & py -3 -c $probe 2>$null } else { $exe = & python -c $probe 2>$null }
            $exe = ($exe | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe.Trim())) { return $exe.Trim() }
        } catch { }
    }
    return $null
}
function Find-Cloudflared {
    $c = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @("${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe", "$env:ProgramFiles\cloudflared\cloudflared.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# Chỉ tắt máy chủ Nhà Nấm (ở bất kỳ thư mục nào, kể cả bản cũ chạy ngầm), không đụng chương trình khác
function Test-NhaNamProcess($p) {
    if ($p.Name -eq "cloudflared.exe") { return $p.CommandLine -like "*tunnel*--url http://localhost:*" }
    if ($p.Name -notmatch "^pythonw?\.exe$" -or $p.CommandLine -notmatch '([A-Za-z]:[\\/][^"]*?server[\\/]app\.py)') { return $false }
    $app = $Matches[1]
    if (-not [IO.Path]::IsPathRooted($app)) { return $false }
    return Test-Path (Join-Path (Split-Path $app) "commands.py")
}

Write-Host "=========================================" -ForegroundColor Green
Write-Host "   NHA NAM AI - web + may chu + link tu xa" -ForegroundColor Green
Write-Host "========================================="

Step "1/5 Tat ban dang chay ngam + bo tu chay khi mo may"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Ok "Da bo che do tu chay cung Windows (tranh gui het han muc tin Zalo)"
}
$port = Get-EnvValue "PORT"; if (-not $port) { $port = "5000" }
Get-CimInstance Win32_Process | Where-Object { Test-NhaNamProcess $_ } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Ok "Da tat ban cu: $($_.Name)" }
# Tu lay code moi nhat tu GitHub (khoi phai nho git pull); loi thi van chay ban dang co
if ((Get-Command git -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $Root ".git"))) {
    $before = git -C $Root rev-parse --short HEAD 2>$null
    $out = git -C $Root pull --ff-only 2>&1
    if ($LASTEXITCODE -ne 0 -and (git -C $Root status --porcelain --untracked-files=no)) {
        # File sua tay chan cap nhat -> cat tam vao git stash (lay lai bang: git stash pop) roi lay code moi
        git -C $Root stash push -m "Nha Nam: tu cat truoc khi cap nhat" 2>&1 | Out-Null
        $out = git -C $Root pull --ff-only 2>&1
        if ($LASTEXITCODE -eq 0) { Warn "Co file sua tay - da cat tam vao git stash de lay code moi" }
    }
    $after = git -C $Root rev-parse --short HEAD 2>$null
    if ($before -ne $after) { Ok "Da cap nhat code moi: $before -> $after" }
    elseif ($out -match "Already up to date|Da cap nhat|up-to-date") { Ok "Code da la ban moi nhat ($after)" }
    else { Warn "Khong tu cap nhat duoc code (mat mang hoac co file sua tay) - van chay ban hien co. Chi tiet: $($out | Select-Object -Last 1)" }
}

Step "2/5 Python"
$py = Find-Python
if (-not $py) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "May chua co Python va winget. Cai Python 3.12 tu python.org (tick 'Add to PATH') roi chay lai."
    }
    Warn "Chua co Python, dang cai Python 3.12 (lan dau hoi lau)..."
    winget install --id Python.Python.3.12 -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
    Refresh-Path
    $py = Find-Python
    if (-not $py) { Fail "Cai Python xong nhung chua tim thay. Dong cua so, bam lai CHAY_NHA_NAM.bat." }
}
Ok $py

Step "3/5 Thu vien Python"
$req = Join-Path $Root "server\requirements.txt"
$marker = Join-Path $Root "server\data\.thu_vien_ok"
$want = (Get-FileHash $req -Algorithm SHA256).Hash + "|" + $py
$have = if (Test-Path $marker) { (Get-Content $marker -Raw).Trim() } else { "" }
if ($have -ne $want) {
    Warn "Dang cai thu vien (chi lan dau)..."
    & $py -m pip install --disable-pip-version-check -q --upgrade pip *> $null
    & $py -m pip install --disable-pip-version-check -q -r $req
    if ($LASTEXITCODE -ne 0) { Fail "Cai thu vien that bai - kiem tra Internet roi chay lai." }
    New-Item -ItemType Directory -Force (Split-Path $marker) | Out-Null
    Set-Content -Path $marker -Value $want -Encoding UTF8
}
Ok "Da co du thu vien"

Step "4/5 Cloudflare Tunnel (link tu xa)"
$cf = Find-Cloudflared
if (-not $cf -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    winget install --id Cloudflare.cloudflared -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
    Refresh-Path
    $cf = Find-Cloudflared
}
if ($cf) { Ok $cf } else { Warn "Khong cai duoc cloudflared - web chi mo duoc trong mang noi bo" }

Step "5/5 Cau hinh (server\.env)"
$firstRun = -not (Test-Path $EnvFile)
if ($firstRun) { Copy-Item (Join-Path $Root "server\.env.example") $EnvFile; Ok "Tao server\.env tu file mau" }
if ($cf) { Set-EnvValue "CLOUDFLARED_PATH" $cf; Set-EnvValue "TUNNEL_ENABLED" "true" }
Set-EnvValue "LOG_FILE" "server/data/server.log"
# Khong can dang nhap: bo mat khau tu dong do bo cai cu tao (mat khau tu dat thi giu nguyen)
if ((Get-EnvValue "APP_PASSWORD") -like "nhanam-*") { Set-EnvValue "APP_PASSWORD" ""; Ok "Bo dang nhap web" }
# Bao cao dinh ky 15 giay/lan lam bot Zalo het han muc -> toi thieu 1 gio/lan
$periodic = 0; [void][int]::TryParse((Get-EnvValue "PERIODIC_REPORT_SEC"), [ref]$periodic)
if ($periodic -gt 0 -and $periodic -lt 600) { Set-EnvValue "PERIODIC_REPORT_SEC" "3600"; Ok "Bao cao Zalo dinh ky: moi 1 gio" }
# Bao MOI lan bat/tat + mach tat/mo lai -> nang han muc cu 12/100 tin len 30/300
if ((Get-EnvValue "ZALO_MAX_PER_HOUR") -eq "12") { Set-EnvValue "ZALO_MAX_PER_HOUR" "30" }
if ((Get-EnvValue "ZALO_MAX_PER_DAY") -eq "100") { Set-EnvValue "ZALO_MAX_PER_DAY" "300" }
if ($firstRun) {
    $key = Read-Host "    Dan API key Gemini (Enter de bo qua)"
    if ($key) {
        Set-EnvValue "LLM_API_KEY" $key.Trim(); Set-EnvValue "LLM_PROVIDER" "openai"
        Set-EnvValue "LLM_BASE_URL" "https://generativelanguage.googleapis.com/v1beta/openai"
        Set-EnvValue "LLM_MODEL" "gemini-3.5-flash-lite"
    }
    $token = Read-Host "    Dan token Zalo Bot (Enter de bo qua)"
    if ($token) { Set-EnvValue "ZALO_BOT_TOKEN" $token.Trim() }
}
Ok "Xong"

$env:OPEN_BROWSER = if ($NoBrowser) { "false" } else { "true" }
Write-Host "`n=========================================" -ForegroundColor Green
Write-Host " Dang chay. Trong may: http://localhost:$port"
Write-Host " Link tu xa se hien ben duoi sau ~30 giay va tu mo trinh duyet."
Write-Host " DONG CUA SO NAY DE TAT (web, may chu AI, link tu xa)." -ForegroundColor Yellow
Write-Host "=========================================`n" -ForegroundColor Green
& $py (Join-Path $Root "server\app.py")
