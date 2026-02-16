# Quick fix for PostgreSQL using default password 'postgres'

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Quick Fix PostgreSQL User" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Set postgres password
$env:PGPASSWORD = "postgres"

Write-Host "[1/4] Testing PostgreSQL connection..." -ForegroundColor Cyan
$testPostgres = & psql -U postgres -d postgres -c "SELECT 1;" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] Cannot connect to PostgreSQL: $testPostgres" -ForegroundColor Red
    Write-Host "  [INFO] Please check if PostgreSQL is running and password is correct" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [OK] Connected to PostgreSQL" -ForegroundColor Green

Write-Host ""
Write-Host "[2/4] Creating/updating user 'jachin'..." -ForegroundColor Cyan

# Check if user exists
$userExists = & psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='jachin';" 2>&1
if ($userExists -match "1") {
    Write-Host "  [INFO] User 'jachin' exists, updating password..." -ForegroundColor Gray
    $updateUser = & psql -U postgres -d postgres -c "ALTER USER jachin WITH PASSWORD 'secure_password';" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] User password updated" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to update user: $updateUser" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  [INFO] Creating user 'jachin'..." -ForegroundColor Gray
    $createUser = & psql -U postgres -d postgres -c "CREATE USER jachin WITH PASSWORD 'secure_password';" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] User created" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to create user: $createUser" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "[3/4] Creating database 'jachin_brain'..." -ForegroundColor Cyan

# Check if database exists
$dbExists = & psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='jachin_brain';" 2>&1
if ($dbExists -match "1") {
    Write-Host "  [INFO] Database 'jachin_brain' exists, updating owner..." -ForegroundColor Gray
    $updateOwner = & psql -U postgres -d postgres -c "ALTER DATABASE jachin_brain OWNER TO jachin;" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Database owner updated" -ForegroundColor Green
    }
} else {
    Write-Host "  [INFO] Creating database 'jachin_brain'..." -ForegroundColor Gray
    $createDb = & psql -U postgres -d postgres -c "CREATE DATABASE jachin_brain OWNER jachin;" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Database created" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to create database: $createDb" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "[4/4] Granting privileges..." -ForegroundColor Cyan
$grantPrivs = & psql -U postgres -d postgres -c "GRANT ALL PRIVILEGES ON DATABASE jachin_brain TO jachin;" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Privileges granted" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Failed to grant privileges: $grantPrivs" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Testing connection with jachin user..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Test connection with jachin user
$env:PGPASSWORD = "secure_password"
$testConn = & psql -U jachin -d jachin_brain -c "SELECT version();" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[SUCCESS] PostgreSQL setup completed!" -ForegroundColor Green
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
    exit 1
}

# Clear password
Remove-Item Env:PGPASSWORD

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  All done! Run check script to verify:" -ForegroundColor Green
Write-Host "  .\scripts\check_local_databases.ps1" -ForegroundColor DarkGray
Write-Host "========================================" -ForegroundColor Cyan
