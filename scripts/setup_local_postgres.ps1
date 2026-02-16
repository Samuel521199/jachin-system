# Setup Local PostgreSQL User and Database
# This script creates the jachin user and database if they don't exist

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setting up Local PostgreSQL" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if PostgreSQL is running
$port5432 = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
if (-not $port5432) {
    Write-Host "[ERROR] PostgreSQL is not running on port 5432" -ForegroundColor Red
    Write-Host "[INFO] Please start PostgreSQL service first" -ForegroundColor Yellow
    exit 1
}

Write-Host "[INFO] PostgreSQL is running" -ForegroundColor Green
Write-Host ""

# Prompt for PostgreSQL superuser password
Write-Host "Please enter PostgreSQL superuser (postgres) password:" -ForegroundColor Yellow
$securePassword = Read-Host -AsSecureString
$postgresPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword))

# Set environment variable for psql
$env:PGPASSWORD = $postgresPassword

Write-Host ""
Write-Host "[1/3] Checking if user 'jachin' exists..." -ForegroundColor Cyan

# Check if user exists
$userExists = & psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='jachin';" 2>&1
if ($userExists -match "1") {
    Write-Host "  [OK] User 'jachin' already exists" -ForegroundColor Green
    
    # Try to update password
    Write-Host "  [INFO] Updating password for user 'jachin'..." -ForegroundColor Gray
    $updatePassword = & psql -U postgres -d postgres -c "ALTER USER jachin WITH PASSWORD 'secure_password';" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Password updated successfully" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] Failed to update password: $updatePassword" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [INFO] Creating user 'jachin'..." -ForegroundColor Gray
    $createUser = & psql -U postgres -d postgres -c "CREATE USER jachin WITH PASSWORD 'secure_password';" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] User 'jachin' created successfully" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to create user: $createUser" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "[2/3] Checking if database 'jachin_brain' exists..." -ForegroundColor Cyan

# Check if database exists
$dbExists = & psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='jachin_brain';" 2>&1
if ($dbExists -match "1") {
    Write-Host "  [OK] Database 'jachin_brain' already exists" -ForegroundColor Green
    
    # Update ownership
    Write-Host "  [INFO] Updating database owner..." -ForegroundColor Gray
    $updateOwner = & psql -U postgres -d postgres -c "ALTER DATABASE jachin_brain OWNER TO jachin;" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Database owner updated" -ForegroundColor Green
    }
} else {
    Write-Host "  [INFO] Creating database 'jachin_brain'..." -ForegroundColor Gray
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

# Grant privileges
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

# Test connection with jachin user
$env:PGPASSWORD = "secure_password"
$testConn = & psql -U jachin -d jachin_brain -c "SELECT version();" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[SUCCESS] PostgreSQL setup completed successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Connection details:" -ForegroundColor Cyan
    Write-Host "  Host: localhost" -ForegroundColor Gray
    Write-Host "  Port: 5432" -ForegroundColor Gray
    Write-Host "  Database: jachin_brain" -ForegroundColor Gray
    Write-Host "  User: jachin" -ForegroundColor Gray
    Write-Host "  Password: secure_password" -ForegroundColor Gray
    Write-Host ""
    Write-Host "DATABASE_URL: postgresql://jachin:secure_password@localhost:5432/jachin_brain" -ForegroundColor DarkGray
} else {
    Write-Host "[ERROR] Connection test failed: $testConn" -ForegroundColor Red
    Write-Host "[INFO] Please check PostgreSQL configuration" -ForegroundColor Yellow
    exit 1
}

# Clear password from environment
Remove-Item Env:PGPASSWORD
