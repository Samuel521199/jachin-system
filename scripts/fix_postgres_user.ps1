# Quick fix for PostgreSQL user authentication
# This script will help fix the jachin user password issue

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Fixing PostgreSQL User Authentication" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if PostgreSQL is running
$port5432 = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
if (-not $port5432) {
    Write-Host "[ERROR] PostgreSQL is not running on port 5432" -ForegroundColor Red
    exit 1
}

Write-Host "[INFO] PostgreSQL is running" -ForegroundColor Green
Write-Host ""

# Try to get postgres password from user
Write-Host "Please enter PostgreSQL superuser (postgres) password:" -ForegroundColor Yellow
Write-Host "(If you don't know it, try pressing Enter for default or check your PostgreSQL installation)" -ForegroundColor Gray
$securePassword = Read-Host -AsSecureString
$postgresPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword))

if ([string]::IsNullOrWhiteSpace($postgresPassword)) {
    Write-Host "[INFO] Trying common default passwords..." -ForegroundColor Yellow
    
    # Try common passwords
    $commonPasswords = @("postgres", "admin", "password", "")
    
    foreach ($pwd in $commonPasswords) {
        $env:PGPASSWORD = $pwd
        Write-Host "  [INFO] Trying password: $($pwd.Length -gt 0 ? '***' : '(empty)')" -ForegroundColor Gray
        
        $test = & psql -U postgres -d postgres -c "SELECT 1;" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $postgresPassword = $pwd
            Write-Host "  [OK] Password found!" -ForegroundColor Green
            break
        }
    }
    
    if ([string]::IsNullOrWhiteSpace($postgresPassword) -or $LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Cannot connect to PostgreSQL with common passwords" -ForegroundColor Red
        Write-Host "[INFO] Please run this command manually:" -ForegroundColor Yellow
        Write-Host "  psql -U postgres -c \"ALTER USER jachin WITH PASSWORD 'secure_password';\"" -ForegroundColor Gray
        Write-Host "  psql -U postgres -c \"CREATE DATABASE jachin_brain OWNER jachin;\"" -ForegroundColor Gray
        exit 1
    }
}

$env:PGPASSWORD = $postgresPassword

Write-Host ""
Write-Host "[1/3] Checking if user 'jachin' exists..." -ForegroundColor Cyan

# Check if user exists
$userCheck = & psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='jachin';" 2>&1
if ($userCheck -match "1") {
    Write-Host "  [OK] User 'jachin' exists" -ForegroundColor Green
    
    # Update password
    Write-Host "  [INFO] Updating password..." -ForegroundColor Gray
    $updatePwd = & psql -U postgres -d postgres -c "ALTER USER jachin WITH PASSWORD 'secure_password';" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Password updated successfully" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to update password: $updatePwd" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  [INFO] User 'jachin' does not exist, creating..." -ForegroundColor Gray
    $createUser = & psql -U postgres -d postgres -c "CREATE USER jachin WITH PASSWORD 'secure_password';" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] User 'jachin' created successfully" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to create user: $createUser" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "[2/3] Checking database 'jachin_brain'..." -ForegroundColor Cyan

# Check if database exists
$dbCheck = & psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='jachin_brain';" 2>&1
if ($dbCheck -match "1") {
    Write-Host "  [OK] Database 'jachin_brain' exists" -ForegroundColor Green
    
    # Update owner
    Write-Host "  [INFO] Updating database owner..." -ForegroundColor Gray
    $updateOwner = & psql -U postgres -d postgres -c "ALTER DATABASE jachin_brain OWNER TO jachin;" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Database owner updated" -ForegroundColor Green
    }
} else {
    Write-Host "  [INFO] Database 'jachin_brain' does not exist, creating..." -ForegroundColor Gray
    $createDb = & psql -U postgres -d postgres -c "CREATE DATABASE jachin_brain OWNER jachin;" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Database 'jachin_brain' created successfully" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to create database: $createDb" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "[3/3] Granting privileges..." -ForegroundColor Cyan

$grantPrivs = & psql -U postgres -d postgres -c "GRANT ALL PRIVILEGES ON DATABASE jachin_brain TO jachin;" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Privileges granted" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Failed to grant privileges: $grantPrivs" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Testing connection..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Test connection
$env:PGPASSWORD = "secure_password"
$testConn = & psql -U jachin -d jachin_brain -c "SELECT version();" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[SUCCESS] PostgreSQL connection test passed!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Connection details:" -ForegroundColor Cyan
    Write-Host "  Host: localhost" -ForegroundColor Gray
    Write-Host "  Port: 5432" -ForegroundColor Gray
    Write-Host "  Database: jachin_brain" -ForegroundColor Gray
    Write-Host "  User: jachin" -ForegroundColor Gray
    Write-Host "  Password: secure_password" -ForegroundColor Gray
} else {
    Write-Host "[ERROR] Connection test failed: $testConn" -ForegroundColor Red
    Write-Host ""
    Write-Host "Try running this manually:" -ForegroundColor Yellow
    Write-Host "  psql -U jachin -d jachin_brain" -ForegroundColor Gray
    exit 1
}

# Clear password
Remove-Item Env:PGPASSWORD

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PostgreSQL user fixed successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
