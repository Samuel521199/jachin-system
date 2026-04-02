# Runs safe_drop_legacy_columns.sql against the same DATABASE_URL as drizzle-kit (from .env.local / .env).
# SQL also: drops legacy iam_*; 0.05 removes non-PK *_not_null; 0.052 removes PK-column *_not_null via temp PK drop + FK restore;
# uuid->text on users.id + FK cols; org/organization_users; Auth PK shape (accounts/sessions/verification_tokens).
# Usage:
#   cd D:\Projects\jachi\jachin-system-main\cloud\nexus
#   .\scripts\run-safe-drop-legacy.ps1
#
# Logs: the SQL file sets client_min_messages TO notice and emits RAISE NOTICE lines
#   prefixed with [jachin-preflight] (0.05 skips, per-table CHECK drops, errors as NOTICE).

$ErrorActionPreference = "Stop"
$nexusRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$sqlPath = Join-Path $PSScriptRoot "safe_drop_legacy_columns.sql"

function Read-DatabaseUrl {
  param([string]$Root)
  foreach ($name in @(".env.local", ".env")) {
    $p = Join-Path $Root $name
    if (-not (Test-Path $p)) { continue }
    foreach ($line in Get-Content $p -Encoding UTF8) {
      $t = $line.Trim()
      if ($t -eq "" -or $t.StartsWith("#")) { continue }
      if ($t -match '^\s*DATABASE_URL\s*=\s*(.+)$') {
        $v = $Matches[1].Trim()
        if (($v.Length -ge 2) -and (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'")))) {
          $v = $v.Substring(1, $v.Length - 2)
        }
        if ($v -ne "") { return $v }
      }
    }
  }
  return $null
}

$dbUrl = Read-DatabaseUrl -Root $nexusRoot
if (-not $dbUrl) {
  Write-Error "DATABASE_URL not found in .env.local or .env under $nexusRoot"
}

if ($dbUrl.StartsWith("postgres://")) {
  $dbUrl = "postgresql://" + $dbUrl.Substring("postgres://".Length)
}

$sql = Get-Content $sqlPath -Raw -Encoding UTF8

$psqlCmd = Get-Command psql -ErrorAction SilentlyContinue
if ($psqlCmd) {
  Write-Host "Using local psql with DATABASE_URL (same as db:push)."
  Write-Host "Watch for NOTICE lines: [jachin-preflight] ... (skip / dropped / FAILED)."
  $sql | & psql $dbUrl -v ON_ERROR_STOP=1 -f -
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  Write-Host "Done."
  exit 0
}

$dockerUrl = $dbUrl
$dockerUrl = $dockerUrl.Replace("@127.0.0.1:", "@host.docker.internal:")
$dockerUrl = $dockerUrl.Replace("@127.0.0.1/", "@host.docker.internal/")
$dockerUrl = $dockerUrl.Replace("@localhost:", "@host.docker.internal:")
$dockerUrl = $dockerUrl.Replace("@localhost/", "@host.docker.internal/")

Write-Host "psql not found; using postgres:16-alpine client (localhost -> host.docker.internal)."
Write-Host "Watch for NOTICE lines: [jachin-preflight] ... (skip / dropped / FAILED)."
$sql | docker run --rm -i postgres:16-alpine psql $dockerUrl -v ON_ERROR_STOP=1 -f -
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Done."
