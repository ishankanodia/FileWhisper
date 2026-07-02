# FileWhisper - one-line installer (Windows 10/11)
#
#   irm https://raw.githubusercontent.com/ishankanodia/FileWhisper/main/install.ps1 | iex
#
# What it does:
#   1. Finds a compatible Python 3.10-3.13 (installs 3.11 via winget if missing)
#   2. Downloads FileWhisper into %USERPROFILE%\.filewhisper\app
#   3. Builds an isolated environment (no PyTorch - stays small & fast)
#   4. Pre-downloads the local AI models (installing the VC++ runtime if needed)
#   5. Puts a "FileWhisper" shortcut (with the logo) on the Desktop. It launches
#      pythonw.exe directly, so starting it shows NO console window.

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocol]::Tls12 } catch {}

$Repo    = "ishankanodia/FileWhisper"
$Branch  = "main"
$AppDir  = Join-Path $env:USERPROFILE ".filewhisper\app"
$Desktop = [Environment]::GetFolderPath("Desktop")

function Fail($lines) {
    Write-Host ""
    foreach ($l in $lines) { Write-Host $l }
    Write-Host ""
}

Write-Host ""
Write-Host "=================================================="
Write-Host "   Installing FileWhisper"
Write-Host "=================================================="
Write-Host ""

# 1. Resolve a python.exe between 3.10 and 3.13. The AI libraries (fastembed,
#    rapidocr, faiss) don't support 3.14+ yet, and fastembed needs 3.10+.
#    Preference order: a known-good pinned version first, newest-installed last.
function Resolve-PythonExe {
    $candidates = @(
        @("py","-3.12"), @("py","-3.11"), @("py","-3.10"), @("py","-3.13"),
        @("py","-3"), @("python"), @("python3")
    )
    foreach ($cand in $candidates) {
        if (Get-Command $cand[0] -ErrorAction SilentlyContinue) {
            try {
                $rest = @(); if ($cand.Length -gt 1) { $rest = $cand[1..($cand.Length - 1)] }
                $out = & $cand[0] @rest -c "import sys; print(sys.executable); print('%d.%d' % sys.version_info[:2])" 2>$null
                if ($LASTEXITCODE -eq 0 -and $out -and @($out).Count -ge 2) {
                    $exe = @($out)[0].Trim(); $ver = @($out)[1].Trim().Split(".")
                    $maj = [int]$ver[0]; $min = [int]$ver[1]
                    if ($maj -eq 3 -and $min -ge 10 -and $min -le 13 -and (Test-Path $exe)) { return $exe }
                }
            } catch {}
        }
    }
    return $null
}

$PythonExe = Resolve-PythonExe
if (-not $PythonExe) {
    Write-Host "-> No compatible Python (3.10 - 3.13) found. Installing Python 3.11 (via winget)..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
        $PythonExe = Resolve-PythonExe
    }
    if (-not $PythonExe) {
        Fail @(
            "Could not set up Python automatically.",
            "FileWhisper needs Python 3.10 - 3.13 (3.14+ is not supported by its AI libraries yet).",
            "Please install Python 3.11 from https://www.python.org/downloads/ (tick 'Add Python to PATH'),",
            "then close this window, open a new PowerShell, and run the install command again."
        )
        return
    }
}

# 2. If FileWhisper is currently running, stop it first - Windows locks the
#    files of a running app, which would make the reinstall below fail.
$PortFile = Join-Path $env:USERPROFILE ".filewhisper\filewhisper.port"
if (Test-Path $PortFile) {
    try {
        $oldPort = (Get-Content $PortFile -Raw).Trim()
        Invoke-RestMethod -Uri "http://127.0.0.1:$oldPort/shutdown" -Method Post -TimeoutSec 3 | Out-Null
        Start-Sleep -Seconds 1
    } catch {}
}

# 3. Download the latest source.
Write-Host "-> Downloading FileWhisper..."
if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
$zip = Join-Path $env:TEMP "filewhisper.zip"
$tmp = Join-Path $env:TEMP "filewhisper_extract"
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$Repo/archive/refs/heads/$Branch.zip" -OutFile $zip
if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $AppDir -Recurse -Force
Remove-Item -Recurse -Force $tmp, $zip

# 4. Build an isolated environment and install dependencies (no PyTorch).
#    $ErrorActionPreference does NOT stop on failing .exe calls, so every
#    native step is checked explicitly - otherwise a failed pip install would
#    still print "installed!" and the app would silently never start.
Write-Host "-> Setting up (downloads ~400 MB the first time, please wait)..."
& $PythonExe -m venv (Join-Path $AppDir ".venv")
$VenvPy  = Join-Path $AppDir ".venv\Scripts\python.exe"
$VenvPyw = Join-Path $AppDir ".venv\Scripts\pythonw.exe"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) {
    Fail @("Could not create the Python environment (using $PythonExe).",
           "Please re-run the installer; if it keeps failing, reinstall Python 3.11 and try again.")
    return
}
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -r (Join-Path $AppDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Fail @("Installing FileWhisper's Python packages failed (see the error above).",
           "Nothing was installed. Check your internet connection and re-run the installer.")
    return
}

# 5. Pre-download the local AI models so the first question doesn't stall.
#    onnxruntime needs the Microsoft Visual C++ runtime; on a clean Windows it
#    is often missing, so install it via winget and retry once.
Write-Host "-> Preparing the local AI models..."
$warmup = "from fastembed import TextEmbedding; list(TextEmbedding('sentence-transformers/all-MiniLM-L6-v2').embed(['warmup'])); print('   embeddings ready')"
& $VenvPy -c $warmup
if ($LASTEXITCODE -ne 0 -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "-> Installing the Microsoft Visual C++ runtime (needed by the AI engine)..."
    winget install -e --id Microsoft.VCRedist.2015+.x64 --silent --accept-package-agreements --accept-source-agreements
    & $VenvPy -c $warmup
}
if ($LASTEXITCODE -ne 0) {
    Fail @("The local AI engine could not start (see the error above).",
           "This usually means the Microsoft Visual C++ runtime is missing. Install it with:",
           "    winget install Microsoft.VCRedist.2015+.x64",
           "then run this installer again.")
    return
}
try { & $VenvPy -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('   OCR ready')" } catch { Write-Host "   (OCR warmup skipped)" }
if ($LASTEXITCODE -ne 0) { Write-Host "   (OCR warmup skipped - scanned PDFs/images may not be readable)" }

# 6. Desktop shortcut with the logo icon, pointing straight at pythonw.exe.
#    pythonw is a GUI-subsystem executable, so no console window ever appears -
#    no VBScript needed (VBScript is deprecated and optional on Windows 11).
#    Stale launchers from older installs are removed.
$Logo = Join-Path $AppDir "filewhisper\static\logo.ico"
Remove-Item (Join-Path $Desktop "Stop FileWhisper.lnk") -ErrorAction SilentlyContinue

$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $Desktop "FileWhisper.lnk"))
$lnk.TargetPath       = $VenvPyw
$lnk.Arguments        = "-m filewhisper.server_launcher"
$lnk.WorkingDirectory = $AppDir
$lnk.IconLocation     = "$Logo,0"
$lnk.Description      = "FileWhisper - chat with your local files"
$lnk.Save()

Write-Host ""
Write-Host "=================================================="
Write-Host "   FileWhisper is installed!"
Write-Host "=================================================="
Write-Host ""
Write-Host "  Double-click  'FileWhisper'  on your Desktop to start."
Write-Host "  It opens in your web browser - no console window."
Write-Host "  To stop it, click  'Quit FileWhisper'  inside the app."
Write-Host "  (If it ever fails to open, check %USERPROFILE%\.filewhisper\filewhisper.log)"
Write-Host ""

# Anonymous, opt-out install ping. Sends ONLY os + version + arch - no personal
# data, no file info. Opt out with:  $env:DO_NOT_TRACK=1  or  $env:FILEWHISPER_NO_ANALYTICS=1
$AnalyticsUrl = "https://your-webhook-endpoint.example/filewhisper-install"  # TODO: set to your Pipedream/webhook URL
if (-not $env:DO_NOT_TRACK -and -not $env:FILEWHISPER_NO_ANALYTICS -and $AnalyticsUrl -notmatch "example") {
    try {
        $body = @{
            event       = "install"
            os          = "windows"
            os_version  = [System.Environment]::OSVersion.Version.ToString()
            arch        = $env:PROCESSOR_ARCHITECTURE
            app_version = "0.1.0"
        } | ConvertTo-Json -Compress
        Invoke-RestMethod -Uri $AnalyticsUrl -Method Post -Body $body -ContentType "application/json" -TimeoutSec 3 | Out-Null
    } catch {}
}
