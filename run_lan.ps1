<#
.SYNOPSIS
  Start the portal for local network (LAN) testing without relying on "python" being on PATH.

.DESCRIPTION
  Searches, in this order, for a Python interpreter that has Streamlit installed:
    1. .venv, venv, env in this folder
    2. the active Conda environment and common Anaconda / Miniconda folders (base + envs)
    3. the Windows Python launcher (py -3) and every interpreter "py -0p" lists
    4. python3 / python on PATH (the Microsoft Store stub is skipped automatically)
    5. python.org install folders (%LOCALAPPDATA%\Programs\Python, Program Files, C:\Python3*)
  If Python exists but Streamlit does not, it offers to create .venv and install requirements.txt.
  Then it prints the LAN URLs and runs:
    <python> -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_lan.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_lan.ps1 -Port 8502
  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_lan.ps1 -Setup      # create .venv without asking
  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_lan.ps1 -CheckOnly  # only show what was found
  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_lan.ps1 -App desktop_app.py -Address 127.0.0.1 -Port 8502
#>
param(
    [string]$App = "app.py",
    [int]$Port = 8501,
    [string]$Address = "0.0.0.0",
    [switch]$Setup,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot
$IsLocal = @("127.0.0.1", "localhost", "::1") -contains $Address
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $App))) {
    Write-Host "$App was not found in $PSScriptRoot." -ForegroundColor Red
    exit 1
}
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Probe = "import sys, importlib.util as u; s = u.find_spec('streamlit'); print(sys.executable); " +
         "print('%d.%d.%d' % sys.version_info[:3]); print(__import__('streamlit').__version__ if s else '')"

function Write-Step([string]$Text) { Write-Host ""; Write-Host "== $Text ==" -ForegroundColor Cyan }

# ------------------------------------------------------------------ candidates
$Candidates = New-Object System.Collections.ArrayList
function Add-Candidate([string]$Exe, [string[]]$Prefix, [string]$Source) {
    foreach ($c in $Candidates) { if ($c.Exe -eq $Exe -and (($c.Prefix -join " ") -eq ($Prefix -join " "))) { return } }
    [void]$Candidates.Add([pscustomobject]@{ Exe = $Exe; Prefix = $Prefix; Source = $Source })
}

foreach ($d in ".venv", "venv", "env") {
    $p = Join-Path $PSScriptRoot "$d\Scripts\python.exe"
    if (Test-Path -LiteralPath $p) { Add-Candidate $p @() "virtual environment '$d'" }
}
if ($env:CONDA_PREFIX) {
    $p = Join-Path $env:CONDA_PREFIX "python.exe"
    if (Test-Path -LiteralPath $p) { Add-Candidate $p @() "active Conda environment" }
}
$CondaRoots = @("$env:USERPROFILE\anaconda3", "$env:USERPROFILE\miniconda3", "$env:USERPROFILE\miniforge3",
                "$env:LOCALAPPDATA\anaconda3", "$env:LOCALAPPDATA\miniconda3",
                "C:\ProgramData\Anaconda3", "C:\ProgramData\Miniconda3")
foreach ($root in $CondaRoots) {
    $p = Join-Path $root "python.exe"
    if (Test-Path -LiteralPath $p) { Add-Candidate $p @() "Conda base ($root)" }
    $envs = Join-Path $root "envs"
    if (Test-Path -LiteralPath $envs) {
        Get-ChildItem -LiteralPath $envs -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $ep = Join-Path $_.FullName "python.exe"
            if (Test-Path -LiteralPath $ep) { Add-Candidate $ep @() "Conda env '$($_.Name)'" }
        }
    }
}
if (Get-Command py -ErrorAction SilentlyContinue) {
    Add-Candidate "py" @("-3") "Windows Python launcher (py -3)"
    try {
        & py -0p 2>$null | ForEach-Object {
            if ($_ -match '([A-Za-z]:\\.*python\.exe)\s*$') { Add-Candidate $Matches[1] @() "listed by 'py -0p'" }
        }
    } catch {}
}
foreach ($n in "python3", "python") {
    Get-Command $n -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object {
        Add-Candidate $_.Source @() "'$n' on PATH"
    }
}
$Patterns = @("$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
              "$env:ProgramFiles\Python3*\python.exe", "C:\Python3*\python.exe")
foreach ($pat in $Patterns) {
    Get-ChildItem -Path $pat -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object {
        Add-Candidate $_.FullName @() "standard install folder"
    }
}

# ------------------------------------------------------------------ probe
function Test-Python($Cand) {
    try {
        $pre = @($Cand.Prefix)
        $out = & $Cand.Exe @pre -c $Probe 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $lines = @($out)
        if ($lines.Count -lt 2) { return $null }
        $st = ""
        if ($lines.Count -gt 2) { $st = "$($lines[2])".Trim() }
        return [pscustomobject]@{ Exe = $Cand.Exe; Prefix = $pre; Source = $Cand.Source; Path = "$($lines[0])".Trim();
                                  Version = "$($lines[1])".Trim(); Streamlit = $st }
    } catch { return $null }
}

function Find-Pythons {
    $found = @(); $seen = @{}
    foreach ($c in $Candidates) {
        $r = Test-Python $c
        if ($null -eq $r) { continue }
        $key = $r.Path.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $found += $r
    }
    return ,$found
}

Write-Step "Looking for Python"
$Pythons = Find-Pythons
if ($Pythons.Count -eq 0) {
    Write-Host "No working Python interpreter was found on this computer." -ForegroundColor Red
    Write-Host "('python' is either missing or only the Microsoft Store shortcut that prints 'Python was not found'.)"
    Write-Host ""
    Write-Host "Install Python 3.12 or newer, then run this launcher again:"
    Write-Host "  winget install -e --id Python.Python.3.12"
    Write-Host "  or download it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'."
    Write-Host "Optional: Settings > Apps > Advanced app settings > App execution aliases > turn off 'python.exe'"
    Write-Host "and 'python3.exe' so the Store shortcut stops hiding the real Python."
    exit 1
}
foreach ($p in $Pythons) {
    $st = if ($p.Streamlit) { "Streamlit $($p.Streamlit)" } else { "no Streamlit" }
    Write-Host ("  {0}  (Python {1}, {2}) - {3}" -f $p.Path, $p.Version, $st, $p.Source)
}

$Selected = $Pythons | Where-Object { $_.Streamlit } | Select-Object -First 1
if ($null -eq $Selected) {
    $Base = $Pythons[0]
    Write-Host ""
    Write-Host "Python was found, but Streamlit is not installed in any of these interpreters." -ForegroundColor Yellow
    $choice = "1"
    if (-not $Setup) {
        Write-Host "  [1] Create a local virtual environment (.venv) with $($Base.Path) and install requirements.txt (recommended)"
        Write-Host "  [2] Install requirements.txt into $($Base.Path)"
        Write-Host "  [Q] Quit"
        $choice = (Read-Host "Choose").Trim().ToUpperInvariant()
    }
    if ($choice -eq "1") {
        Write-Step "Creating .venv"
        & $Base.Exe @($Base.Prefix) -m venv .venv
        $venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvPy)) { Write-Host "Creating the virtual environment failed." -ForegroundColor Red; exit 1 }
        & $venvPy -m pip install --upgrade pip
        & $venvPy -m pip install -r requirements.txt
        $Selected = Test-Python ([pscustomobject]@{ Exe = $venvPy; Prefix = @(); Source = "virtual environment '.venv'" })
    } elseif ($choice -eq "2") {
        Write-Step "Installing requirements"
        & $Base.Exe @($Base.Prefix) -m pip install -r requirements.txt
        $Selected = Test-Python ([pscustomobject]@{ Exe = $Base.Exe; Prefix = $Base.Prefix; Source = $Base.Source })
    } else {
        Write-Host "Cancelled."; exit 1
    }
    if ($null -eq $Selected -or -not $Selected.Streamlit) {
        Write-Host "Streamlit is still not available. Check the pip output above (internet connection, proxy)." -ForegroundColor Red
        exit 1
    }
}
Write-Host ""
Write-Host "Using: $($Selected.Path) (Python $($Selected.Version), Streamlit $($Selected.Streamlit))" -ForegroundColor Green

# ------------------------------------------------------------------ port + addresses
function Test-PortFree([int]$P) {
    try { $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $P); $l.Start(); $l.Stop(); return $true }
    catch { return $false }
}
if (-not (Test-PortFree $Port)) {
    $old = $Port
    for ($i = $Port + 1; $i -le $Port + 10; $i++) { if (Test-PortFree $i) { $Port = $i; break } }
    if ($Port -eq $old) { Write-Host "Ports $old-$($old + 10) are all busy. Close the other app or pass -Port." -ForegroundColor Red; exit 1 }
    Write-Host "Port $old is already in use (is the app already running?). Using port $Port instead." -ForegroundColor Yellow
}

$Ips = @()
try {
    $Ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -ExpandProperty IPAddress
} catch {
    $Ips = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
        Where-Object { $_.AddressFamily -eq "InterNetwork" -and -not $_.ToString().StartsWith("127.") } |
        ForEach-Object { $_.ToString() }
}
if ($IsLocal) {
    Write-Step "Open this on this computer"
    Write-Host "  http://localhost:$Port" -ForegroundColor Green
    Write-Host "  (bound to ${Address}; other devices on the network cannot open it)"
} else {
    Write-Step "Open one of these on your phone, tablet or another PC (same Wi-Fi / LAN)"
    if (@($Ips).Count -eq 0) { Write-Host "  No network address found. Connect to Wi-Fi or Ethernet." -ForegroundColor Yellow }
    foreach ($ip in $Ips) { Write-Host "  http://${ip}:$Port" -ForegroundColor Green }
    Write-Host "  On this computer: http://localhost:$Port"
    Write-Host ""
    Write-Host "If another device cannot connect: allow Python on PRIVATE networks when Windows Firewall asks, make sure"
    Write-Host "the Wi-Fi profile is Private, and avoid guest networks / VPNs. Firewall rule (run as Administrator):"
    Write-Host "  netsh advfirewall firewall add rule name=`"Streamlit $Port`" dir=in action=allow protocol=TCP localport=$Port profile=private"
}

$cmdShown = if ($Selected.Prefix.Count -gt 0) { "$($Selected.Exe) $($Selected.Prefix -join ' ')" } else { "`"$($Selected.Path)`"" }
Write-Host ""
Write-Host "Command: $cmdShown -m streamlit run $App --server.address $Address --server.port $Port"
if ($CheckOnly) { exit 0 }

Write-Step "Starting $App (press Ctrl+C to stop)"
$env:QS_LAUNCHER = if ($App -eq "app.py") { "run_lan.ps1" } else { "run_desktop.bat" }
& $Selected.Exe @($Selected.Prefix) -m streamlit run $App --server.address $Address --server.port $Port
exit $LASTEXITCODE
