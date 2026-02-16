# Grant database permissions to the application user
# Fixes: InsufficientPrivilegeError / "Permission denied for table skills"
# Run from project root or installer directory. Uses DATABASE_URL from .env.

param(
    [string]$SuperUser = "postgres",
    [string]$SuperUserPassword = "",
    [switch]$DryRun = $false
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
if ((Split-Path -Leaf $projectRoot) -eq "installer") {
    $projectRoot = Split-Path -Parent $projectRoot
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Grant app user table permissions" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$envPath = Join-Path $projectRoot ".env"
if (-not (Test-Path $envPath)) {
    Write-Host "[ERROR] .env not found at $envPath" -ForegroundColor Red
    Write-Host "  Set DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DATABASE" -ForegroundColor Gray
    exit 1
}

$envContent = Get-Content $envPath -Raw
if ($envContent -notmatch "DATABASE_URL=(.+)") {
    Write-Host "[ERROR] DATABASE_URL not found in .env" -ForegroundColor Red
    exit 1
}

$databaseUrl = $matches[1].Trim()
# Parse postgresql://user:password@host:port/dbname or postgresql://user@host:port/dbname
if ($databaseUrl -match "postgresql://([^:@]+)(?::([^@]+))?@([^:]+):(\d+)/([^\s]+)") {
    $appUser = $matches[1]
    $dbHost = $matches[3]
    $dbPort = $matches[4]
    $dbName = $matches[5]
} elseif ($databaseUrl -match "postgresql://([^:@]+)(?::([^@]+))?@([^/]+)/([^\s]+)") {
    $appUser = $matches[1]
    $dbHost = $matches[3]
    $dbPort = "5432"
    $dbName = $matches[4]
} else {
    Write-Host "[ERROR] Could not parse DATABASE_URL" -ForegroundColor Red
    exit 1
}

Write-Host "[INFO] App user from .env: $appUser" -ForegroundColor Cyan
Write-Host "[INFO] Database: $dbName @ ${dbHost}:${dbPort}" -ForegroundColor Cyan
Write-Host ""

$sqlPath = Join-Path $PSScriptRoot "grant_app_permissions.sql"
if (-not (Test-Path $sqlPath)) {
    Write-Host "[ERROR] SQL file not found: $sqlPath" -ForegroundColor Red
    exit 1
}

$sql = Get-Content $sqlPath -Raw
$sql = $sql -replace "APP_USER", $appUser

if ($DryRun) {
    Write-Host "[DRY RUN] Generated SQL:" -ForegroundColor Yellow
    Write-Host $sql
    exit 0
}

$tempSql = [System.IO.Path]::GetTempFileName() + ".sql"
# Write UTF-8 without BOM so psql on Windows does not fail with encoding error
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($tempSql, $sql, $utf8NoBom)
try {
    # Prefer psql if available
    $psql = Get-Command psql -ErrorAction SilentlyContinue
    if ($psql) {
        $env:PGPASSWORD = $SuperUserPassword
        $env:PGHOST = $dbHost
        $env:PGPORT = $dbPort
        $env:PGUSER = $SuperUser
        $env:PGDATABASE = $dbName
        Write-Host "[INFO] Running GRANTs as superuser ($SuperUser) via psql..." -ForegroundColor Cyan
        & psql -f $tempSql 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[WARN] psql returned non-zero. You may need to run SQL manually as postgres." -ForegroundColor Yellow
            Write-Host "[INFO] Generated SQL saved to: $tempSql" -ForegroundColor Gray
            Write-Host "[INFO] Example: psql -U postgres -d $dbName -f $tempSql" -ForegroundColor Gray
        } else {
            Write-Host "[OK] Permissions granted." -ForegroundColor Green
        }
    } else {
        Write-Host "[INFO] psql not in PATH. Writing SQL to temp file for manual run." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Run as PostgreSQL superuser (e.g. postgres):" -ForegroundColor Cyan
        Write-Host "  psql -U postgres -d $dbName -f `"$tempSql`"" -ForegroundColor White
        Write-Host ""
        Write-Host "Or paste the following SQL in pgAdmin / psql:" -ForegroundColor Cyan
        Write-Host $sql -ForegroundColor Gray
    }
} finally {
    if (Test-Path $tempSql) {
        # Keep temp file so user can run manually if needed
        Write-Host ""
        Write-Host "SQL file: $tempSql" -ForegroundColor Gray
    }
}
