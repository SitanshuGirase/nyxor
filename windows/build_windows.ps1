<#
 Builds NYXOR for Windows. Produces, in dist\ :
   NYXOR-Setup.exe                      a normal installer (double-click, Start menu icon, uninstaller)   [needs Inno Setup 6]
   msix\NYXOR_<version>_x64.msix        the package you upload to the Microsoft Store                    [needs the Windows SDK]
 The GitHub workflow in .github\workflows runs this for you in the cloud: beginners never need to run it by hand.
 By hand (Windows 10/11 x64, Python 3.12 on PATH), from the project folder:
   powershell -ExecutionPolicy Bypass -File windows\build_windows.ps1 -IdentityName "<Partner Center name>" -Publisher "CN=<id>" -PublisherDisplay "<your name>"
 Store identity values come from Partner Center > your app > Product management > Product identity. Without them the defaults below
 still build a working installer; the .msix then is only for testing, not for upload.
 Not yet run on Windows by the author: expect small fixes on the first run.
#>
param(
  [string]$IdentityName = "NYXOR.Local", [string]$Publisher = "CN=NYXOR Local", [string]$PublisherDisplay = "NYXOR",
  [string]$SupportEmail = "", [string]$PrivacyUrl = "",
  [string]$Version = "2.3.0.0",      # four parts, last must be 0 for the Store; raise it for every submission
  [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
$dist = Join-Path $root "dist"; $out = Join-Path $dist "msix"; $stage = Join-Path $out "layout"
Remove-Item -Recurse -Force $dist -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$stage\NYXOR", "$stage\Assets" | Out-Null

if (-not $SkipTests) { python server.py --selftest; if ($LASTEXITCODE -ne 0) { throw "selftest failed: do not package" } }

# icons are drawn at build time (Pillow), then Store config + marker file (the marker switches on the Store build's stricter limits)
python -m pip install --upgrade pillow pyinstaller pywebview
python windows\make_icons.py
@{publisher=$PublisherDisplay; support_email=$SupportEmail; privacy_url=$PrivacyUrl} | ConvertTo-Json | Set-Content -Encoding utf8 store_config.json
Set-Content -Encoding ascii STORE_BUILD "Store build: stricter privacy and safety limits (see privacy.py)"

# freeze: PyInstaller is a build-time tool only (the backend itself stays standard-library only)
$pi = @("--noconfirm","--clean","--windowed","--onedir","--name","NYXOR","--distpath","$out\dist","--workpath","$out\work","--specpath","$out","--paths",".","--icon","$root\windows\Assets\app.ico")
foreach ($f in "index.html","style.css","core.js","app.js","privacy.html","smoke.js","nyxor_colab_video.ipynb","store_config.json","STORE_BUILD") { $pi += @("--add-data","$root\$f;.") }
$pi += @("--add-data","$root\fonts;fonts")
foreach ($m in "webview","server","assets","privacy","budget","telemetry","vault","db","catalog","providers","modes","router","council","agent","research","websearch","attachments","workspace","videogen","tts","terminal") { $pi += @("--hidden-import",$m) }
python -m PyInstaller @pi "windows\launcher.py"
if (-not (Test-Path "$out\dist\NYXOR\NYXOR.exe")) { throw "PyInstaller did not produce NYXOR.exe" }

# 1. the normal installer
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe","$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
  $env:NYXOR_PUBLISHER = $PublisherDisplay
  & $iscc "/DSourceDir=$out\dist\NYXOR" "/DAppVersion=$($Version.Substring(0,$Version.LastIndexOf('.')))" "windows\installer.iss"
  Write-Host "Built $dist\NYXOR-Setup.exe"
} else { Write-Warning "Inno Setup 6 not found: skipped NYXOR-Setup.exe" }

# 2. the Store package
Copy-Item -Recurse "$out\dist\NYXOR\*" "$stage\NYXOR\"
Copy-Item "$root\windows\Assets\*.png" "$stage\Assets\"
foreach ($n in "StoreLogo","Square44x44Logo","Square71x71Logo","Square150x150Logo","Square310x310Logo","Wide310x150Logo","SplashScreen") {
  Copy-Item "$stage\Assets\$n.scale-100.png" "$stage\Assets\$n.png"      # base names the manifest points at; scale-NNN variants are picked automatically
}
(Get-Content "$root\windows\AppxManifest.xml" -Raw).Replace("@@IDENTITY_NAME@@",$IdentityName).Replace("@@PUBLISHER@@",$Publisher).Replace("@@PUBLISHER_DISPLAY@@",$PublisherDisplay).Replace("@@VERSION@@",$Version) | Set-Content -Encoding utf8 "$stage\AppxManifest.xml"
$mk = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\makeappx.exe" -ErrorAction SilentlyContinue | Sort-Object FullName | Select-Object -Last 1
if ($mk) { & $mk.FullName pack /d $stage /p "$out\NYXOR_$Version`_x64.msix" /o; Write-Host "Built $out\NYXOR_$Version`_x64.msix (unsigned: upload it to Partner Center, the Store signs it)" }
else { Write-Warning "makeappx.exe not found (Windows SDK): skipped the .msix" }
