# FileWhisper - one-line installer (Windows 10/11)
#
#   irm https://raw.githubusercontent.com/ishankanodia/server_rag/main/install.ps1 | iex
#
# What it does:
#   1. Finds Python (installs it via winget if missing)
#   2. Downloads FileWhisper into %USERPROFILE%\.filewhisper\app
#   3. Builds an isolated environment (no PyTorch - stays small & fast)
#   4. Pre-downloads the local AI models
#   5. Puts "FileWhisper" and "Stop FileWhisper" shortcuts (with the logo) on the
#      Desktop. Starting it shows NO console window.

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocol]::Tls12 } catch {}

$Repo    = "ishankanodia/server_rag"
$Branch  = "main"
$AppDir  = Join-Path $env:USERPROFILE ".filewhisper\app"
$PidFile = Join-Path $env:USERPROFILE ".filewhisper\filewhisper.pid"
$Desktop = [Environment]::GetFolderPath("Desktop")

Write-Host ""
Write-Host "=================================================="
Write-Host "   Installing FileWhisper"
Write-Host "=================================================="
Write-Host ""

# 1. Resolve a concrete python.exe. Try the py launcher, then python/python3.
function Resolve-PythonExe {
    foreach ($cand in @(@("py","-3"), @("python"), @("python3"))) {
        if (Get-Command $cand[0] -ErrorAction SilentlyContinue) {
            try {
                $rest = @(); if ($cand.Length -gt 1) { $rest = $cand[1..($cand.Length - 1)] }
                $exe = & $cand[0] @rest -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe.Trim())) { return $exe.Trim() }
            } catch {}
        }
    }
    return $null
}

$PythonExe = Resolve-PythonExe
if (-not $PythonExe) {
    Write-Host "-> Python not found. Installing Python 3.11 (via winget)..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
        $PythonExe = Resolve-PythonExe
    }
    if (-not $PythonExe) {
        Write-Host ""
        Write-Host "Could not set up Python automatically."
        Write-Host "Please install it from https://www.python.org/downloads/ (tick 'Add Python to PATH'),"
        Write-Host "then close this window, open a new PowerShell, and run the install command again."
        return
    }
}

# 2. Download the latest source.
Write-Host "-> Downloading FileWhisper..."
if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
$zip = Join-Path $env:TEMP "filewhisper.zip"
$tmp = Join-Path $env:TEMP "filewhisper_extract"
Invoke-WebRequest -Uri "https://github.com/$Repo/archive/refs/heads/$Branch.zip" -OutFile $zip
if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $AppDir -Recurse -Force
Remove-Item -Recurse -Force $tmp, $zip

# 3. Build an isolated environment and install dependencies (no PyTorch).
Write-Host "-> Setting up (downloads ~400 MB the first time, please wait)..."
& $PythonExe -m venv (Join-Path $AppDir ".venv")
$VenvPy  = Join-Path $AppDir ".venv\Scripts\python.exe"
$VenvPyw = Join-Path $AppDir ".venv\Scripts\pythonw.exe"
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -r (Join-Path $AppDir "requirements.txt")

# 4. Pre-download the local AI models so the first question doesn't stall.
Write-Host "-> Preparing the local AI models..."
& $VenvPy -c "from fastembed import TextEmbedding; list(TextEmbedding('sentence-transformers/all-MiniLM-L6-v2').embed(['warmup'])); print('   embeddings ready')"
try { & $VenvPy -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('   OCR ready')" } catch { Write-Host "   (OCR warmup skipped)" }

# 5. Hidden launchers (VBScript runs pythonw with no console window).
$Logo     = Join-Path $AppDir "filewhisper\static\logo.ico"
$VbsStart = Join-Path $AppDir "FileWhisper.vbs"
$VbsStop  = Join-Path $AppDir "StopFileWhisper.vbs"

@"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$AppDir"
sh.Run """$VenvPyw"" -m filewhisper.server_launcher", 0, False
"@ | Set-Content -Encoding ASCII -Path $VbsStart

@"
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell -NoProfile -WindowStyle Hidden -Command ""if (Test-Path '$PidFile') { Stop-Process -Id (Get-Content '$PidFile') -Force -ErrorAction SilentlyContinue }""", 0, True
"@ | Set-Content -Encoding ASCII -Path $VbsStop

# 6. Desktop shortcuts with the logo icon.
$ws = New-Object -ComObject WScript.Shell

$lnk = $ws.CreateShortcut((Join-Path $Desktop "FileWhisper.lnk"))
$lnk.TargetPath       = "wscript.exe"
$lnk.Arguments        = """$VbsStart"""
$lnk.WorkingDirectory = $AppDir
$lnk.IconLocation     = "$Logo,0"
$lnk.Description       = "FileWhisper - chat with your local files"
$lnk.Save()

$lnk2 = $ws.CreateShortcut((Join-Path $Desktop "Stop FileWhisper.lnk"))
$lnk2.TargetPath   = "wscript.exe"
$lnk2.Arguments    = """$VbsStop"""
$lnk2.IconLocation = "$Logo,0"
$lnk2.Description   = "Stop FileWhisper"
$lnk2.Save()

Write-Host ""
Write-Host "=================================================="
Write-Host "   FileWhisper is installed!"
Write-Host "=================================================="
Write-Host ""
Write-Host "  Double-click  'FileWhisper'  on your Desktop to start."
Write-Host "  It opens in your web browser - no console window."
Write-Host "  To stop it, double-click  'Stop FileWhisper'."
Write-Host ""
