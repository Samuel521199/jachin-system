# Smoke: sign dummy file, upload MinIO, POST /api/v1/admin/desktop-releases
# Requires: pip install boto3; minisign.exe; ms.key
# Load secrets from cloud/nexus/.env.local (run from cloud/nexus: .\scripts\smoke-publish-desktop-release.ps1)

param(
  [string]$Version = "0.0.1",
  [string]$MinisignExe = "",
  [string]$SecretKey = ""
)

if (-not $MinisignExe) {
  if ($env:MINISIGN_EXE) { $MinisignExe = $env:MINISIGN_EXE }
  else { $MinisignExe = 'D:\tools\minisign-win64\x86_64\minisign.exe' }
}
if (-not $SecretKey) {
  $SecretKey = 'D:\tools\ms.key'
}

$ErrorActionPreference = "Stop"
$nexusRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$envFile = Join-Path $nexusRoot ".env.local"

if (-not (Test-Path $envFile)) {
  Write-Error "Missing $envFile - copy .env.example to .env.local first."
}

$dq = [char]34
$sq = [char]39
Get-Content -LiteralPath $envFile -Encoding UTF8 | ForEach-Object {
  $line = $_.Trim()
  if (-not $line -or $line.StartsWith("#")) { return }
  if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }
  $eq = $line.IndexOf("=")
  if ($eq -lt 1) { return }
  $key = $line.Substring(0, $eq).Trim()
  $val = $line.Substring($eq + 1).Trim()
  if ($val.Length -ge 2 -and $val[0] -eq $val[-1] -and ($val[0] -eq $dq -or $val[0] -eq $sq)) {
    $val = $val.Substring(1, $val.Length - 2)
  }
  if (-not $key) { return }
  if (-not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($key, "Process"))) { return }
  Set-Item -Path ("env:" + $key) -Value $val
}

if (-not $env:NEXUS_BASE_URL) {
  if ($env:AUTH_URL) {
    try {
      $u = [Uri]$env:AUTH_URL.Trim()
      $env:NEXUS_BASE_URL = ($u.Scheme + "://" + $u.Authority)
    } catch {
      $env:NEXUS_BASE_URL = "http://127.0.0.1:3000"
    }
  } elseif ($env:NEXTAUTH_URL) {
    try {
      $u = [Uri]$env:NEXTAUTH_URL.Trim()
      $env:NEXUS_BASE_URL = ($u.Scheme + "://" + $u.Authority)
    } catch {
      $env:NEXUS_BASE_URL = "http://127.0.0.1:3000"
    }
  } else {
    $env:NEXUS_BASE_URL = "http://127.0.0.1:3000"
  }
}

$required = @(
  "NEXUS_ADMIN_SECRET",
  "DESKTOP_RELEASES_S3_ENDPOINT",
  "DESKTOP_RELEASES_S3_BUCKET",
  "DESKTOP_RELEASES_S3_ACCESS_KEY",
  "DESKTOP_RELEASES_S3_SECRET_KEY"
)
foreach ($k in $required) {
  $v = [Environment]::GetEnvironmentVariable($k, "Process")
  if ([string]::IsNullOrWhiteSpace($v)) {
    Write-Error ("Missing env: " + $k)
  }
}

if (-not (Test-Path -LiteralPath $MinisignExe)) {
  Write-Error ("minisign not found: " + $MinisignExe)
}
if (-not (Test-Path -LiteralPath $SecretKey)) {
  Write-Error ("Secret key not found: " + $SecretKey)
}

$tmp = Join-Path $env:TEMP ("jachin-smoke-" + $Version)
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$dummy = Join-Path $tmp "dummy.exe"
Set-Content -LiteralPath $dummy -Value "smoke" -NoNewline

Write-Host ("Signing: " + $dummy)
# minisign 0.12 default output is "<file>.minisig" (not .sig). Use -x to write a fixed path for Nexus/publish script.
$dummyAbs = (Resolve-Path -LiteralPath $dummy).Path
$sigPath = Join-Path $tmp "dummy.exe.sig"
$sk = (Resolve-Path -LiteralPath $SecretKey).Path
& $MinisignExe -S -s $sk -m $dummyAbs -x $sigPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path -LiteralPath $sigPath)) {
  $alt = Join-Path $tmp "dummy.exe.minisig"
  if (Test-Path -LiteralPath $alt) {
    $sigPath = $alt
  } else {
    $any = Get-ChildItem -LiteralPath $tmp -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Extension -eq ".sig" -or $_.Extension -eq ".minisig" } |
      Select-Object -First 1
    if ($any) { $sigPath = $any.FullName }
  }
}
if (-not (Test-Path -LiteralPath $sigPath)) {
  Write-Error "Signature file not found after minisign. Expected -x output or dummy.exe.minisig in temp dir."
}

$objectKey = "desktop/releases/" + $Version + "/windows-x86_64/jachin-desktop-" + $Version + "-windows-x86_64.exe"
$pubDate = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$env:_SIG_PATH = $sigPath
$env:_TMP_DUMMY = $dummy
$env:_OBJECT_KEY = $objectKey
$env:_VERSION = $Version
$env:_PUB_DATE = $pubDate

$pyFile = Join-Path $PSScriptRoot "smoke-publish-desktop-release-body.py"
Write-Host ("NEXUS_BASE_URL=" + $env:NEXUS_BASE_URL)
python $pyFile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
