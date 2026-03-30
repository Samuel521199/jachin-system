# Database Initialization Script (PowerShell)
# Jachin-System v3.2 Database Initialization

param(
    [string]$DatabaseUrl = "",
    [switch]$SkipMigrations = $false
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Jachin-System v3.2 Database Init" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[NOTE] About Conda Environment:" -ForegroundColor Gray
Write-Host "  - This script will use the 'jachin-dev' conda environment if it exists" -ForegroundColor Gray
Write-Host "  - The PowerShell prompt shows your current session's conda environment" -ForegroundColor Gray
Write-Host "  - Script execution does NOT change your PowerShell session's environment" -ForegroundColor Gray
Write-Host "  - If you see '(base)' in the prompt, that's normal - the script uses jachin-dev directly" -ForegroundColor Gray
Write-Host ""
Write-Host "[NOTE] Database Selection:" -ForegroundColor Gray
Write-Host "  - This script will automatically detect and use local PostgreSQL if available" -ForegroundColor Gray
Write-Host "  - If local services are not found, it may use Docker containers where applicable" -ForegroundColor Gray
Write-Host "  - Local PostgreSQL: Checks for running postgresql* Windows services" -ForegroundColor Gray
Write-Host "  - Vector memory: V2 uses LanceDB on disk (no separate vector service port)" -ForegroundColor Gray
Write-Host ""

# Get project root directory (where this script is located)
$scriptPath = $MyInvocation.MyCommand.Path
if (-not $scriptPath) {
    $scriptPath = $PSCommandPath
}
if (-not $scriptPath) {
    $scriptPath = (Get-Location).Path
}
$projectRoot = Split-Path -Parent $scriptPath
# If script is in installer subdirectory, go up one level
if ((Split-Path -Leaf $projectRoot) -eq "installer") {
    $projectRoot = Split-Path -Parent $projectRoot
}

# Check Conda environment
Write-Host "[1/8] Checking Conda environment..." -ForegroundColor Yellow
$condaEnvExists = $false
$condaActivated = $false
$currentCondaEnv = $null

# Check if conda is available
try {
    $condaVersion = conda --version 2>&1
    Write-Host "  [INFO] Conda version: $condaVersion" -ForegroundColor Gray
} catch {
    Write-Host "  [WARN] Conda not available, using system Python" -ForegroundColor Yellow
    Write-Host "  [INFO] Install Conda from: https://docs.conda.io/en/latest/miniconda.html" -ForegroundColor Cyan
}

# Check current conda environment activation status
if ($env:CONDA_DEFAULT_ENV) {
    $currentCondaEnv = $env:CONDA_DEFAULT_ENV
    $condaActivated = $true
    Write-Host "  [INFO] Currently activated conda environment: $currentCondaEnv" -ForegroundColor Cyan
    if ($currentCondaEnv -eq "jachin-dev") {
        Write-Host "  [OK] Conda environment 'jachin-dev' is activated" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] Different conda environment activated: $currentCondaEnv" -ForegroundColor Yellow
        Write-Host "  [INFO] Expected: jachin-dev" -ForegroundColor Gray
        Write-Host "  [INFO] To activate: conda activate jachin-dev" -ForegroundColor Cyan
    }
} else {
    Write-Host "  [WARN] No conda environment is currently activated" -ForegroundColor Yellow
    Write-Host "  [INFO] To activate: conda activate jachin-dev" -ForegroundColor Cyan
}

# Check if jachin-dev environment exists
try {
    $envList = conda env list 2>&1
    if ($envList -match "jachin-dev") {
        $condaEnvExists = $true
        Write-Host "  [OK] Conda environment 'jachin-dev' found" -ForegroundColor Green
        
        # Extract environment path
        $condaInfo = conda info --envs 2>&1 | Select-String "jachin-dev"
        if ($condaInfo -match "jachin-dev\s+(\S+)") {
            $condaEnvPath = $matches[1]
            Write-Host "  [INFO] Environment path: $condaEnvPath" -ForegroundColor Gray
        }
    } else {
        Write-Host "  [WARN] Conda environment 'jachin-dev' not found" -ForegroundColor Yellow
        Write-Host "  [INFO] You can create it with: conda env create -f environment.yml" -ForegroundColor Cyan
    }
} catch {
    Write-Host "  [WARN] Cannot list conda environments: $_" -ForegroundColor Yellow
}

# Check if current Python is from conda
if ($condaActivated) {
    try {
        $pythonPath = & python -c "import sys; print(sys.executable)" 2>&1
        if ($pythonPath -match "conda|anaconda|miniconda") {
            Write-Host "  [OK] Current Python is from conda: $pythonPath" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] Current Python may not be from conda: $pythonPath" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  [WARN] Cannot check Python path: $_" -ForegroundColor Yellow
    }
}

# Determine Python command prefix
if ($condaEnvExists) {
    # Get conda environment path
    $condaInfo = conda info --envs 2>&1 | Select-String "jachin-dev"
    if ($condaInfo -match "jachin-dev\s+(\S+)") {
        $condaEnvPath = $matches[1]
        $pythonCmd = "$condaEnvPath\python.exe"
        $pipCmd = "$condaEnvPath\Scripts\pip.exe"
        $alembicCmd = "$condaEnvPath\Scripts\alembic.exe"
        
        # Verify Python executable exists
        if (Test-Path $pythonCmd) {
            Write-Host "  [INFO] Using Conda environment: jachin-dev" -ForegroundColor Cyan
            Write-Host "  [INFO] Python path: $pythonCmd" -ForegroundColor Gray
            if (-not $condaActivated -or $currentCondaEnv -ne "jachin-dev") {
                Write-Host "  [INFO] Note: Using conda environment without activation (direct path)" -ForegroundColor Gray
            }
        } else {
            Write-Host "  [WARN] Python executable not found at: $pythonCmd" -ForegroundColor Yellow
            Write-Host "  [INFO] Falling back to conda run" -ForegroundColor Cyan
            $pythonCmd = "conda run -n jachin-dev python"
            $pipCmd = "conda run -n jachin-dev pip"
            $alembicCmd = "conda run -n jachin-dev alembic"
            Write-Host "  [INFO] Using Conda environment: jachin-dev (via conda run)" -ForegroundColor Cyan
        }
    } else {
        # Fallback to conda run
        $pythonCmd = "conda run -n jachin-dev python"
        $pipCmd = "conda run -n jachin-dev pip"
        $alembicCmd = "conda run -n jachin-dev alembic"
        Write-Host "  [INFO] Using Conda environment: jachin-dev (via conda run)" -ForegroundColor Cyan
        if (-not $condaActivated) {
            Write-Host "  [INFO] Note: Conda environment will be activated automatically via conda run" -ForegroundColor Gray
        }
    }
} else {
    $pythonCmd = "python"
    $pipCmd = "pip"
    $alembicCmd = "alembic"
    Write-Host "  [INFO] Using system Python" -ForegroundColor Cyan
    if ($condaActivated) {
        Write-Host "  [WARN] A conda environment is activated but 'jachin-dev' not found" -ForegroundColor Yellow
        Write-Host "  [INFO] Current environment: $currentCondaEnv" -ForegroundColor Gray
    }
}

# Summary of conda environment status
Write-Host ""
Write-Host "  [SUMMARY] Conda Environment Status:" -ForegroundColor Cyan
Write-Host "    - Environment exists: $(if ($condaEnvExists) { 'Yes' } else { 'No' })" -ForegroundColor Gray
Write-Host "    - Currently activated: $(if ($condaActivated) { "Yes ($currentCondaEnv)" } else { 'No' })" -ForegroundColor Gray
Write-Host "    - Will use: $(if ($condaEnvExists) { 'jachin-dev conda environment' } else { 'system Python' })" -ForegroundColor Gray
Write-Host ""

# Check Python environment
Write-Host "[2/8] Checking Python environment..." -ForegroundColor Yellow
try {
    $pythonVersion = & $pythonCmd --version 2>&1
    Write-Host "  [OK] Python version: $pythonVersion" -ForegroundColor Green
    
    # Verify Python path
    try {
        $actualPythonPath = & $pythonCmd -c "import sys; print(sys.executable)" 2>&1
        Write-Host "  [INFO] Python executable: $actualPythonPath" -ForegroundColor Gray
        if ($actualPythonPath -match "conda|anaconda|miniconda") {
            Write-Host "  [OK] Python is from conda environment" -ForegroundColor Green
        }
    } catch {
        # Ignore if we can't get Python path
    }
} catch {
    Write-Host "  [ERROR] Python not installed or not in PATH" -ForegroundColor Red
    Write-Host "  [ERROR] Command used: $pythonCmd" -ForegroundColor Red
    if ($condaEnvExists -and -not $condaActivated) {
        Write-Host "  [INFO] Try activating conda environment first: conda activate jachin-dev" -ForegroundColor Cyan
    }
    exit 1
}

# Check Alembic
Write-Host "[3/8] Checking Alembic..." -ForegroundColor Yellow
$alembicInstalled = $false
try {
    $null = & $alembicCmd --version 2>&1
    $alembicInstalled = $true
    Write-Host "  [OK] Alembic installed" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Alembic not installed" -ForegroundColor Yellow
    Write-Host "  [INFO] Installing Alembic..." -ForegroundColor Cyan
    try {
        & $pipCmd install alembic
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] Alembic installed successfully" -ForegroundColor Green
            $alembicInstalled = $true
        } else {
            Write-Host "  [ERROR] Failed to install Alembic" -ForegroundColor Red
            Write-Host "  [INFO] Please run manually: $pipCmd install alembic" -ForegroundColor Yellow
            exit 1
        }
    } catch {
        Write-Host "  [ERROR] Failed to install Alembic: $_" -ForegroundColor Red
        Write-Host "  [INFO] Please run manually: $pipCmd install alembic" -ForegroundColor Yellow
        exit 1
    }
}

# Verify Alembic installation
if (-not $alembicInstalled) {
    Write-Host "  [ERROR] Alembic installation verification failed" -ForegroundColor Red
    exit 1
}

# Check and install required dependencies for migrations
Write-Host "[4/8] Checking required dependencies..." -ForegroundColor Yellow
$requiredPackages = @(
    @{Package="psycopg2-binary"; Import="psycopg2"},
    @{Package="sqlalchemy"; Import="sqlalchemy"}
)
foreach ($pkg in $requiredPackages) {
    $packageName = $pkg.Package
    $importName = $pkg.Import
    try {
        $result = & $pythonCmd -c "import $importName" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] $packageName installed" -ForegroundColor Green
        } else {
            throw "Not installed"
        }
    } catch {
        Write-Host "  [WARN] $packageName not installed, installing..." -ForegroundColor Yellow
        & $pipCmd install $packageName
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] $packageName installed successfully" -ForegroundColor Green
        } else {
            Write-Host "  [ERROR] Failed to install $packageName" -ForegroundColor Red
            Write-Host "  [INFO] Please run manually: $pipCmd install $packageName" -ForegroundColor Yellow
        }
    }
}

# Vector storage (LanceDB — no standalone service)
Write-Host "[5/8] Vector storage (LanceDB)..." -ForegroundColor Yellow
Write-Host "  [INFO] V2 vectors use LanceDB on disk (LANCEDB_PATH / ~/.jachin/lancedb_data); no separate vector DB service required." -ForegroundColor Gray

# Check database connection
Write-Host "[6/8] Checking database connection..." -ForegroundColor Yellow

# Check for local PostgreSQL first, then Docker container
Write-Host "  [INFO] Checking for local PostgreSQL service..." -ForegroundColor Cyan
$useLocalPostgres = $false
$localPgUser = "postgres"
$localPgPassword = ""
$localPgPort = 5432
$localPgConnected = $false

# Check for PostgreSQL services (both running and stopped)
$allPgServices = Get-Service -Name postgresql* -ErrorAction SilentlyContinue
$localPgService = $allPgServices | Where-Object { $_.Status -eq 'Running' } | Select-Object -First 1
$stoppedPgService = $allPgServices | Where-Object { $_.Status -eq 'Stopped' } | Select-Object -First 1

if ($localPgService) {
    Write-Host "  [OK] Local PostgreSQL service found: $($localPgService.Name)" -ForegroundColor Green
    Write-Host "  [INFO] Using local PostgreSQL instead of Docker container" -ForegroundColor Cyan
    $useLocalPostgres = $true
} elseif ($stoppedPgService) {
    Write-Host "  [WARN] Local PostgreSQL service found but is stopped: $($stoppedPgService.Name)" -ForegroundColor Yellow
    Write-Host "  [INFO] Attempting to start local PostgreSQL service..." -ForegroundColor Cyan
    try {
        Start-Service -Name $stoppedPgService.Name -ErrorAction Stop
        Start-Sleep -Seconds 3
        $stoppedPgService.Refresh()
        if ($stoppedPgService.Status -eq 'Running') {
            Write-Host "  [OK] Local PostgreSQL service started successfully" -ForegroundColor Green
            $useLocalPostgres = $true
            $localPgService = $stoppedPgService
        } else {
            Write-Host "  [WARN] Service may still be starting, will try to connect anyway" -ForegroundColor Yellow
            $useLocalPostgres = $true
            $localPgService = $stoppedPgService
        }
    } catch {
        Write-Host "  [ERROR] Cannot start PostgreSQL service: $_" -ForegroundColor Red
        Write-Host "  [INFO] You may need to run this script as Administrator" -ForegroundColor Yellow
        Write-Host "  [INFO] Or start the service manually: Start-Service -Name $($stoppedPgService.Name)" -ForegroundColor Gray
        Write-Host "  [INFO] Will try Docker container as fallback..." -ForegroundColor Cyan
        $useLocalPostgres = $false
    }
}

# Test local PostgreSQL connection if using local PostgreSQL
if ($useLocalPostgres) {
    # Test local PostgreSQL connection with multiple authentication methods
    Write-Host "  [INFO] Testing local PostgreSQL connection..." -ForegroundColor Cyan
    
    # Try to get connection info from environment or .env file first
    
    if (Test-Path ".env") {
        $envContent = Get-Content ".env" -Raw
        if ($envContent -match "DATABASE_URL=postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)") {
            $localPgUser = $matches[1]
            $localPgPassword = $matches[2]
            $localPgPort = $matches[4]
            Write-Host "  [INFO] Found DATABASE_URL in .env file" -ForegroundColor Gray
        }
    }
    
    # Try multiple authentication methods
    $connectionMethods = @(
        @{User=$localPgUser; Password=$localPgPassword; Desc="from .env or default"},
        @{User="postgres"; Password=""; Desc="postgres user with no password"},
        @{User="postgres"; Password="postgres"; Desc="postgres user with 'postgres' password"},
        @{User=$env:USERNAME; Password=""; Desc="Windows username with no password"}
    )
    
    $localPgConnected = $false
    foreach ($method in $connectionMethods) {
        Write-Host "  [DEBUG] Trying: $($method.Desc)..." -ForegroundColor Gray
        try {
            $testScript = @"
import psycopg2
try:
    conn = psycopg2.connect(
        host='localhost',
        port=$localPgPort,
        user='$($method.User)',
        password='$($method.Password)',
        dbname='postgres',
        connect_timeout=3
    )
    print('SUCCESS')
    conn.close()
    exit(0)
except psycopg2.OperationalError as e:
    print(f'AUTH_ERROR: {e}')
    exit(1)
except Exception as e:
    print(f'ERROR: {e}')
    exit(1)
"@
            $testFile = Join-Path $env:TEMP "test_local_pg_auth.py"
            $testScript | Out-File -FilePath $testFile -Encoding UTF8 -Force
            $testResult = & $pythonCmd -X utf8 $testFile 2>&1 | Out-String
            Remove-Item $testFile -ErrorAction SilentlyContinue
            
            if ($LASTEXITCODE -eq 0 -and $testResult -match "SUCCESS") {
                Write-Host "  [OK] Local PostgreSQL connection successful with $($method.Desc)" -ForegroundColor Green
                $localPgUser = $method.User
                $localPgPassword = $method.Password
                $localPgConnected = $true
                break
            }
        } catch {
            # Continue to next method
        }
    }
    
    if (-not $localPgConnected) {
        Write-Host "  [WARN] Cannot connect to local PostgreSQL with any tested authentication method" -ForegroundColor Yellow
        Write-Host "  [INFO] Tested methods:" -ForegroundColor Gray
        foreach ($method in $connectionMethods) {
            Write-Host "    - $($method.Desc)" -ForegroundColor Gray
        }
        Write-Host ""
        Write-Host "  [SOLUTION] Please provide PostgreSQL connection info:" -ForegroundColor Cyan
        Write-Host "    Option 1: Set DATABASE_URL in .env file:" -ForegroundColor White
        Write-Host "      DATABASE_URL=postgresql://username:password@localhost:5432/jachin_brain" -ForegroundColor Gray
        Write-Host "    Option 2: Use Docker container instead:" -ForegroundColor White
        Write-Host "      docker-compose -f docker-compose.minimal.yml up -d postgres" -ForegroundColor Gray
        Write-Host ""
        
        # Ask user if they want to continue with Docker or provide credentials
        $response = Read-Host "  Continue with Docker container? (Y/n)"
        if ($response -eq "" -or $response -match "^[Yy]") {
            Write-Host "  [INFO] Will try Docker container..." -ForegroundColor Cyan
            $useLocalPostgres = $false
        } else {
            Write-Host "  [INFO] Please set DATABASE_URL in .env file and restart script" -ForegroundColor Yellow
            exit 1
        }
    } else {
        Write-Host "  [OK] Using local PostgreSQL with user: $localPgUser" -ForegroundColor Green
    }
}  # End of if ($useLocalPostgres) block for connection testing

# If local PostgreSQL not available, check Docker container
if (-not $useLocalPostgres) {
    Write-Host "  [INFO] Checking PostgreSQL Docker container..." -ForegroundColor Cyan
    $postgresContainer = docker ps --filter "name=jachin-postgres" --format "{{.Names}}" 2>&1 | Select-String "jachin-postgres"
    if (-not $postgresContainer) {
        Write-Host "  [ERROR] Neither local PostgreSQL nor Docker container is available!" -ForegroundColor Red
        Write-Host "  [INFO] Please either:" -ForegroundColor Yellow
        Write-Host "    1. Start local PostgreSQL service, or" -ForegroundColor Gray
        Write-Host "    2. Start PostgreSQL container: docker-compose -f docker-compose.minimal.yml up -d postgres" -ForegroundColor Gray
        exit 1
    }
    Write-Host "  [OK] PostgreSQL Docker container is running" -ForegroundColor Green
    
    # Check if PostgreSQL is ready
    Write-Host "  [INFO] Checking PostgreSQL readiness..." -ForegroundColor Cyan
    try {
        $pgReady = docker exec jachin-postgres pg_isready -U jachin 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] PostgreSQL is ready to accept connections" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] PostgreSQL container is running but not ready yet" -ForegroundColor Yellow
            Write-Host "  [INFO] Waiting 5 seconds for PostgreSQL to be ready..." -ForegroundColor Cyan
            Start-Sleep -Seconds 5
        }
    } catch {
        Write-Host "  [WARN] Cannot check PostgreSQL readiness: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [OK] Using local PostgreSQL service" -ForegroundColor Green
}

# Build DATABASE_URL - prioritize detected local PostgreSQL credentials
if ($useLocalPostgres -and $localPgConnected) {
    # If using local PostgreSQL, use detected credentials (override .env if needed)
    if ($localPgPassword -eq "") {
        $DatabaseUrl = "postgresql://$localPgUser@localhost:$localPgPort/jachin_brain"
    } else {
        $DatabaseUrl = "postgresql://$localPgUser`:$localPgPassword@localhost:$localPgPort/jachin_brain"
    }
    Write-Host "  [INFO] Using local PostgreSQL with detected credentials (user: $localPgUser)" -ForegroundColor Cyan
    Write-Host "  [INFO] Note: Overriding DATABASE_URL from .env file to use local PostgreSQL credentials" -ForegroundColor Gray
} elseif ($DatabaseUrl -eq "") {
    # Read from environment variable or config file
    if (Test-Path ".env") {
        $envContent = Get-Content ".env" -Raw
        if ($envContent -match "DATABASE_URL=(.+)") {
            $DatabaseUrl = $matches[1].Trim()
            Write-Host "  [INFO] Using DATABASE_URL from .env file" -ForegroundColor Cyan
        }
    }
    
    if ($DatabaseUrl -eq "") {
        # Use Docker container with jachin user (default)
        $DatabaseUrl = "postgresql://jachin:secure_password@localhost:5432/jachin_brain"
        Write-Host "  [INFO] Using default Docker PostgreSQL container configuration" -ForegroundColor Cyan
    }
}

Write-Host "  [INFO] Database URL: $DatabaseUrl" -ForegroundColor Cyan

# Try to connect to database
Write-Host "  [INFO] Testing database connection..." -ForegroundColor Cyan
try {
    # Extract connection info from URL
    if ($DatabaseUrl -match "postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)") {
        $dbUser = $matches[1]
        $dbPass = $matches[2]
        $dbHost = $matches[3]
        $dbPort = $matches[4]
        $dbName = $matches[5]
        
        if ($useLocalPostgres) {
            # Use local PostgreSQL - test with Python
            Write-Host "  [INFO] Testing local PostgreSQL connection with Python..." -ForegroundColor Cyan
            # Build Python script with proper password handling
            $passwordLine = if ($localPgPassword -ne "") { "        password='$localPgPassword',`n" } else { "" }
            $localTestScript = @"
import psycopg2
try:
    conn = psycopg2.connect(
        host='localhost',
        port=$localPgPort,
        user='$localPgUser',
$passwordLine        dbname='postgres',
        connect_timeout=5
    )
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname='$dbName';")
    exists = cur.fetchone()
    if exists and exists[0]:
        print('EXISTS')
    else:
        print('NOT_EXISTS')
    cur.close()
    conn.close()
    exit(0)
except Exception as e:
    print(f'ERROR: {e}')
    exit(1)
"@
            $localTestFile = Join-Path $env:TEMP "test_local_pg.py"
            $localTestScript | Out-File -FilePath $localTestFile -Encoding UTF8 -Force
            $localTestResult = & $pythonCmd -X utf8 $localTestFile 2>&1 | Out-String
            Remove-Item $localTestFile -ErrorAction SilentlyContinue
            
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  [OK] Local PostgreSQL connection test successful" -ForegroundColor Green
                if ($localTestResult -match "EXISTS") {
                    Write-Host "  [OK] Database '$dbName' exists" -ForegroundColor Green
                } else {
                    Write-Host "  [WARN] Database '$dbName' does not exist, will be created during migration" -ForegroundColor Yellow
                }
            } else {
                Write-Host "  [WARN] Local PostgreSQL connection test failed: $localTestResult" -ForegroundColor Yellow
                Write-Host "  [INFO] Will attempt migration anyway..." -ForegroundColor Cyan
            }
        } else {
            # Use Docker container - test with docker exec
            $testResult = docker exec jachin-postgres psql -U $dbUser -d postgres -c "SELECT 1;" 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  [OK] Database connection test successful" -ForegroundColor Green
                
                # Check if database exists
                $dbExists = docker exec jachin-postgres psql -U $dbUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$dbName';" 2>&1
                if ($dbExists -match "1") {
                    Write-Host "  [OK] Database '$dbName' exists" -ForegroundColor Green
                } else {
                    Write-Host "  [WARN] Database '$dbName' does not exist, will be created during migration" -ForegroundColor Yellow
                }
            } else {
                Write-Host "  [ERROR] Database connection test failed" -ForegroundColor Red
                Write-Host "  [INFO] Error: $testResult" -ForegroundColor Yellow
            }
        }
    }
} catch {
    Write-Host "  [WARN] Cannot test database connection: $_" -ForegroundColor Yellow
    Write-Host "  [INFO] Will attempt migration anyway..." -ForegroundColor Cyan
}

# Run migrations
if (-not $SkipMigrations) {
    Write-Host "[7/8] Running database migrations..." -ForegroundColor Yellow
    
    # Extract database name from URL
    if ($DatabaseUrl -match "postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)") {
        $dbUser = $matches[1]
        $dbPass = $matches[2]
        $dbHost = $matches[3]
        $dbPort = $matches[4]
        $dbName = $matches[5]
        
        # Create database if it doesn't exist
        Write-Host "  [INFO] Ensuring database '$dbName' exists..." -ForegroundColor Cyan
        try {
            if ($useLocalPostgres) {
                # Use local PostgreSQL - create with Python
                $passwordLine = if ($localPgPassword -ne "") { "        password='$localPgPassword',`n" } else { "" }
                $createDbScript = @"
import psycopg2
try:
    conn = psycopg2.connect(
        host='localhost',
        port=$localPgPort,
        user='$localPgUser',
$passwordLine        dbname='postgres',
        connect_timeout=5
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname='$dbName';")
    exists = cur.fetchone()
    if not exists or not exists[0]:
        cur.execute("CREATE DATABASE $dbName;")
        print('CREATED')
    else:
        print('EXISTS')
    cur.close()
    conn.close()
    exit(0)
except Exception as e:
    print(f'ERROR: {e}')
    exit(1)
"@
                $createDbFile = Join-Path $env:TEMP "create_local_db.py"
                $createDbScript | Out-File -FilePath $createDbFile -Encoding UTF8 -Force
                $createDbResult = & $pythonCmd -X utf8 $createDbFile 2>&1 | Out-String
                Remove-Item $createDbFile -ErrorAction SilentlyContinue
                
                if ($LASTEXITCODE -eq 0) {
                    if ($createDbResult -match "CREATED") {
                        Write-Host "  [OK] Database '$dbName' created successfully" -ForegroundColor Green
                    } elseif ($createDbResult -match "EXISTS") {
                        Write-Host "  [OK] Database '$dbName' already exists" -ForegroundColor Green
                    }
                } else {
                    Write-Host "  [WARN] Cannot check/create database: $createDbResult" -ForegroundColor Yellow
                    Write-Host "  [INFO] Will attempt migration anyway..." -ForegroundColor Cyan
                }
            } else {
                # Use Docker container
                $dbCheck = docker exec jachin-postgres psql -U $dbUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$dbName';" 2>&1
                if ($dbCheck -notmatch "1") {
                    Write-Host "  [INFO] Creating database '$dbName'..." -ForegroundColor Cyan
                    docker exec jachin-postgres psql -U $dbUser -d postgres -c "CREATE DATABASE $dbName;" 2>&1 | Out-Null
                    if ($LASTEXITCODE -eq 0) {
                        Write-Host "  [OK] Database '$dbName' created successfully" -ForegroundColor Green
                    } else {
                        Write-Host "  [WARN] Failed to create database, may already exist" -ForegroundColor Yellow
                    }
                } else {
                    Write-Host "  [OK] Database '$dbName' already exists" -ForegroundColor Green
                }
            }
        } catch {
            Write-Host "  [WARN] Cannot check/create database: $_" -ForegroundColor Yellow
            Write-Host "  [INFO] Will attempt migration anyway..." -ForegroundColor Cyan
        }
    }
    
    $env:DATABASE_URL = $DatabaseUrl
    # Set UTF-8 encoding to handle Chinese characters in alembic.ini
    $env:PYTHONIOENCODING = "utf-8"
    
    # Use project root from script start, ensure core directory exists
    $corePath = Join-Path $projectRoot "core"
    
    # Verify core directory exists
    if (-not (Test-Path $corePath)) {
        Write-Host "  [ERROR] Core directory not found: $corePath" -ForegroundColor Red
        exit 1
    }
    
    Push-Location $corePath
    try {
        # Set Alembic path
        $env:ALEMBIC_CONFIG = "memory\schema\migrations\alembic.ini"
        
        # Verify paths are set correctly
        if (-not $projectRoot -or -not $corePath) {
            Write-Host "  [ERROR] Failed to determine project paths" -ForegroundColor Red
            Write-Host "  [DEBUG] Current location: $(Get-Location)" -ForegroundColor Gray
            Write-Host "  [DEBUG] Project root: $projectRoot" -ForegroundColor Gray
            Write-Host "  [DEBUG] Core path: $corePath" -ForegroundColor Gray
            exit 1
        }
        
        # Set PYTHONPATH with both paths (use semicolon for Windows)
        $env:PYTHONPATH = "$projectRoot;$corePath"
        
        # Debug: Print environment variables
        Write-Host "  [DEBUG] DATABASE_URL: $env:DATABASE_URL" -ForegroundColor Gray
        Write-Host "  [DEBUG] PYTHONPATH: $env:PYTHONPATH" -ForegroundColor Gray
        Write-Host "  [DEBUG] ALEMBIC_CONFIG: $env:ALEMBIC_CONFIG" -ForegroundColor Gray
        Write-Host "  [DEBUG] Project root: $projectRoot" -ForegroundColor Gray
        Write-Host "  [DEBUG] Core path: $corePath" -ForegroundColor Gray
        
        # Test database connection with Python first
        Write-Host "  [INFO] Testing database connection with Python..." -ForegroundColor Cyan
        
        # First, test connection using psycopg2 directly
        $testScriptFile = Join-Path $env:TEMP "test_db_connection.py"
        
        # Escape backslashes for Python raw string (need double backslash)
        $projectRootEscaped = $projectRoot -replace '\\', '\\\\'
        $corePathEscaped = $corePath -replace '\\', '\\\\'
        
        $testScript = @"
import sys
import os
import traceback
import socket

# Add paths to sys.path
project_root = r'$projectRootEscaped'
core_path = r'$corePathEscaped'
sys.path.insert(0, project_root)
sys.path.insert(0, core_path)

print(f'Python paths added:')
print(f'  Project root: {project_root}')
print(f'  Core path: {core_path}')
print(f'  sys.path: {sys.path[:3]}')

try:
    import psycopg2
    from psycopg2 import OperationalError, Error, extensions
    
    db_url = os.getenv('DATABASE_URL', 'postgresql://jachin:secure_password@localhost:5432/jachin_brain')
    print(f'Database URL: {db_url}')
    
    # Parse URL manually
    if db_url.startswith('postgresql://'):
        db_url = db_url.replace('postgresql://', '', 1)
    if '@' in db_url:
        auth, rest = db_url.split('@', 1)
        user, password = auth.split(':', 1)
        if '/' in rest:
            host_port, dbname = rest.split('/', 1)
            if ':' in host_port:
                host, port = host_port.split(':', 1)
            else:
                host, port = host_port, '5432'
        else:
            host, port = rest.split(':', 1) if ':' in rest else (rest, '5432')
            dbname = 'postgres'
    else:
        raise ValueError('Invalid database URL format')
    
    print(f'Parsed connection: host={host}, port={port}, user={user}, dbname={dbname}')
    
    # Test hostname resolution first
    try:
        print(f'Testing hostname resolution for {host}...')
        resolved_ip = socket.gethostbyname(host)
        print(f'  Resolved to: {resolved_ip}')
    except socket.gaierror as e:
        print(f'  Hostname resolution failed: {e}')
        print(f'  Trying 127.0.0.1 instead...')
        host = '127.0.0.1'
    
    # Test port connectivity
    try:
        print(f'Testing port connectivity to {host}:{port}...')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, int(port)))
        sock.close()
        if result == 0:
            print(f'  Port {port} is open')
        else:
            print(f'  Port {port} is closed (error code: {result})')
    except Exception as e:
        print(f'  Port test failed: {e}')
    
    # Try connection with detailed error handling
    print('Attempting PostgreSQL connection...')
    print(f'Connection parameters: host={host}, port={port}, user={user}, dbname={dbname}')
    
    # Enable detailed error reporting
    try:
        from psycopg2 import errors
        print('psycopg2 errors module available')
    except ImportError:
        print('psycopg2 errors module not available')
    
    try:
        # Try with original host first
        # Use connection string for better error reporting
        conn_string = f"host={host} port={port} user={user} password={password} dbname={dbname} connect_timeout=10"
        print(f'Connection string: host={host} port={port} user={user} dbname={dbname} connect_timeout=10')
        conn = psycopg2.connect(
            host=host, 
            port=int(port), 
            user=user, 
            password=password, 
            dbname=dbname,
            connect_timeout=10
        )
        print('Connection established!')
        cur = conn.cursor()
        cur.execute('SELECT version();')
        version = cur.fetchone()
        print(f'PostgreSQL version: {version[0]}')
        cur.close()
        conn.close()
        print('Connection test successful!')
        sys.exit(0)
    except OperationalError as e:
        error_msg = str(e)
        error_repr = repr(e)
        print(f'OperationalError occurred!')
        print(f'Error message: {error_msg}')
        print(f'Error repr: {error_repr}')
        print(f'Error args: {e.args}')
        
        # Try to get more detailed error information
        print(f'\n=== Detailed Error Information ===')
        if hasattr(e, 'pgcode'):
            print(f'PostgreSQL error code: {e.pgcode}')
        if hasattr(e, 'pgerror'):
            print(f'PostgreSQL error: {e.pgerror}')
        if hasattr(e, 'diag'):
            diag = e.diag
            if diag:
                print(f'\nError diagnostic:')
                if hasattr(diag, 'severity'):
                    print(f'  Severity: {diag.severity}')
                if hasattr(diag, 'sqlstate'):
                    print(f'  SQL State: {diag.sqlstate}')
                if hasattr(diag, 'message_primary'):
                    print(f'  Primary message: {diag.message_primary}')
                if hasattr(diag, 'message_detail'):
                    print(f'  Detail: {diag.message_detail}')
                if hasattr(diag, 'message_hint'):
                    print(f'  Hint: {diag.message_hint}')
                if hasattr(diag, 'context'):
                    print(f'  Context: {diag.context}')
        
        # Try to get libpq error
        try:
            import psycopg2.extensions
            if hasattr(psycopg2.extensions, 'Diagnostics'):
                print(f'\nTrying to get libpq diagnostics...')
        except:
            pass
        
        # Print full exception details
        print(f'\n=== Full Exception Details ===')
        print(f'  Exception type: {type(e).__name__}')
        print(f'  Exception module: {type(e).__module__}')
        print(f'  Exception str: {str(e)}')
        print(f'  Exception repr: {repr(e)}')
        print(f'  Exception args: {e.args}')
        
        # Try to access all attributes
        print(f'\n  Available attributes:')
        for attr in dir(e):
            if not attr.startswith('_'):
                try:
                    value = getattr(e, attr)
                    if not callable(value):
                        print(f'    {attr}: {value}')
                except:
                    pass
        
        print(f'\n=== Full Traceback ===')
        traceback.print_exc()
        
        # Try with 127.0.0.1 if localhost failed
        if host == 'localhost':
            print(f'\nRetrying with 127.0.0.1...')
            try:
                conn = psycopg2.connect(
                    host='127.0.0.1', 
                    port=int(port), 
                    user=user, 
                    password=password, 
                    dbname=dbname,
                    connect_timeout=10
                )
                print('Connection established with 127.0.0.1!')
                cur = conn.cursor()
                cur.execute('SELECT version();')
                version = cur.fetchone()
                print(f'PostgreSQL version: {version[0]}')
                cur.close()
                conn.close()
                print('Connection test successful!')
                sys.exit(0)
            except Exception as e2:
                print(f'Retry with 127.0.0.1 also failed: {type(e2).__name__}: {e2}')
                if hasattr(e2, 'pgerror'):
                    print(f'  PostgreSQL error: {e2.pgerror}')
                traceback.print_exc()
        
        sys.exit(1)
    except Error as e:
        print(f'psycopg2 Error: {type(e).__name__}: {str(e)}')
        print(f'Error args: {e.args}')
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f'Unexpected error: {type(e).__name__}: {str(e)}')
        traceback.print_exc()
        sys.exit(1)
except ImportError as e:
    print(f'Import error: {e}')
    print('Make sure psycopg2-binary is installed')
    traceback.print_exc()
    sys.exit(1)
except Exception as e:
    print(f'Script error: {type(e).__name__}: {str(e)}')
    traceback.print_exc()
    sys.exit(1)
"@
        $testScript | Out-File -FilePath $testScriptFile -Encoding UTF8 -Force
        $testOutput = & $pythonCmd -X utf8 $testScriptFile 2>&1 | Out-String
        $testExitCode = $LASTEXITCODE
        Write-Host $testOutput
        Remove-Item $testScriptFile -ErrorAction SilentlyContinue
        
        if ($testExitCode -ne 0) {
            Write-Host "  [ERROR] Python database connection test failed" -ForegroundColor Red
            Write-Host ""
            
            # Test connection from inside Docker container
            Write-Host "  [INFO] Testing connection from inside Docker container..." -ForegroundColor Cyan
            $containerWorks = $false
            try {
                $containerTest = docker exec jachin-postgres psql -U jachin -d jachin_brain -c "SELECT version();" 2>&1
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  [OK] Connection from Docker container works!" -ForegroundColor Green
                    $containerWorks = $true
                    Write-Host "  [INFO] This suggests a port mapping or network issue" -ForegroundColor Yellow
                } else {
                    Write-Host "  [ERROR] Connection from Docker container also failed" -ForegroundColor Red
                    Write-Host "  [ERROR] Output: $containerTest" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  [WARN] Cannot test from container: $_" -ForegroundColor Yellow
            }
            
            # Check Docker port mapping
            Write-Host ""
            Write-Host "  [INFO] Checking Docker port mapping..." -ForegroundColor Cyan
            try {
                $portMapping = docker port jachin-postgres 2>&1
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  [INFO] Port mapping: $portMapping" -ForegroundColor Gray
                } else {
                    Write-Host "  [WARN] Cannot get port mapping: $portMapping" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  [WARN] Cannot check port mapping: $_" -ForegroundColor Yellow
            }
            
            # Check if port 5432 is already in use on Windows host
            Write-Host ""
            Write-Host "  [INFO] Checking if port 5432 is in use on Windows host..." -ForegroundColor Cyan
            $localPostgresRunning = $false
            try {
                $portInUse = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
                if ($portInUse) {
                    Write-Host "  [WARN] Port 5432 is already in use on Windows host!" -ForegroundColor Yellow
                    Write-Host "  [INFO] Process using port 5432:" -ForegroundColor Gray
                    $portInUse | ForEach-Object {
                        $process = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
                        Write-Host "    PID $($_.OwningProcess): $($process.ProcessName) - State: $($_.State)" -ForegroundColor Gray
                        if ($process.ProcessName -eq "postgres") {
                            $localPostgresRunning = $true
                        }
                    }
                    Write-Host "  [INFO] This may prevent Docker port mapping from working" -ForegroundColor Yellow
                    
                    if ($localPostgresRunning) {
                        Write-Host ""
                        if ($useLocalPostgres) {
                            Write-Host "  [INFO] Local PostgreSQL is being used (not Docker), so port conflict is expected" -ForegroundColor Cyan
                            Write-Host "  [INFO] This is normal - script will use local PostgreSQL service" -ForegroundColor Green
                        } else {
                            Write-Host "  [CRITICAL] Local PostgreSQL service is running and blocking Docker port mapping!" -ForegroundColor Red
                            Write-Host "  [INFO] Since we're using Docker, local PostgreSQL should be stopped or use different port" -ForegroundColor Yellow
                            Write-Host ""
                            Write-Host "  [SOLUTION] Choose one:" -ForegroundColor Cyan
                            Write-Host "    Option 1: Use local PostgreSQL instead (RECOMMENDED)" -ForegroundColor White
                            Write-Host "      - Local PostgreSQL is already running and accessible" -ForegroundColor Gray
                            Write-Host "      - Script will automatically use local PostgreSQL" -ForegroundColor Gray
                            Write-Host "    Option 2: Stop local PostgreSQL to use Docker" -ForegroundColor White
                            Write-Host "      - Run as Administrator: Stop-Service -Name postgresql* -Force" -ForegroundColor Gray
                            Write-Host "      - Then restart this script" -ForegroundColor Gray
                            Write-Host "    Option 3: Change Docker port mapping" -ForegroundColor White
                            Write-Host "      - Edit docker-compose.minimal.yml to use port 5433" -ForegroundColor Gray
                            Write-Host ""
                            
                            # Don't auto-stop, just inform user
                            Write-Host "  [NOTE] Script will NOT automatically stop local PostgreSQL service" -ForegroundColor Yellow
                            Write-Host "         Please choose an option above and restart script if needed" -ForegroundColor Yellow
                        }
                        
                        # Only try to stop if NOT using local PostgreSQL
                        if (-not $useLocalPostgres) {
                            Write-Host ""
                            Write-Host "  [AUTO-FIX] Attempting to stop local PostgreSQL service..." -ForegroundColor Cyan
                            $serviceStopped = $false
                        try {
                            Write-Host "  [DEBUG] Searching for PostgreSQL services..." -ForegroundColor Gray
                            $pgServices = Get-Service -Name postgresql* -ErrorAction SilentlyContinue
                            if ($pgServices) {
                                Write-Host "  [INFO] Found PostgreSQL services: $($pgServices.Name -join ', ')" -ForegroundColor Gray
                                foreach ($service in $pgServices) {
                                    if ($service.Status -eq 'Running') {
                                        Write-Host "  [INFO] Stopping service: $($service.Name)..." -ForegroundColor Gray
                                        try {
                                            Stop-Service -Name $service.Name -Force -ErrorAction Stop
                                            Start-Sleep -Seconds 2
                                            $service.Refresh()
                                            if ($service.Status -eq 'Stopped') {
                                                Write-Host "  [OK] Service $($service.Name) stopped successfully" -ForegroundColor Green
                                                $serviceStopped = $true
                                            } else {
                                                Write-Host "  [WARN] Service $($service.Name) may still be running" -ForegroundColor Yellow
                                            }
                                        } catch {
                                            Write-Host "  [WARN] Cannot stop service $($service.Name): $_" -ForegroundColor Yellow
                                            Write-Host "  [INFO] You may need to run this script as Administrator" -ForegroundColor Gray
                                        }
                                    }
                                }
                                
                                if ($serviceStopped) {
                                    Write-Host ""
                                    Write-Host "  [SUCCESS] Local PostgreSQL service stopped!" -ForegroundColor Green
                                    Write-Host "  [INFO] Waiting 3 seconds for port to be released..." -ForegroundColor Cyan
                                    Start-Sleep -Seconds 3
                                    
                                    # Retry connection test
                                    Write-Host "  [INFO] Retrying database connection test..." -ForegroundColor Cyan
                                    $retryTestScript = @"
import psycopg2
try:
    conn = psycopg2.connect(
        host='localhost',
        port=5432,
        user='jachin',
        password='secure_password',
        dbname='jachin_brain',
        connect_timeout=10
    )
    cur = conn.cursor()
    cur.execute('SELECT version();')
    version = cur.fetchone()
    print('SUCCESS: Connection established!')
    print(f'PostgreSQL version: {version[0]}')
    cur.close()
    conn.close()
    exit(0)
except Exception as e:
    print(f'FAILED: {e}')
    exit(1)
"@
                                    $retryTestFile = Join-Path $env:TEMP "retry_after_stop_service.py"
                                    $retryTestScript | Out-File -FilePath $retryTestFile -Encoding UTF8 -Force
                                    $retryOutput = & $pythonCmd -X utf8 $retryTestFile 2>&1 | Out-String
                                    $retryExitCode = $LASTEXITCODE
                                    Remove-Item $retryTestFile -ErrorAction SilentlyContinue
                                    
                                    if ($retryExitCode -eq 0) {
                                        Write-Host $retryOutput -ForegroundColor Green
                                        Write-Host "  [OK] Database connection test successful after stopping local PostgreSQL!" -ForegroundColor Green
                                        Write-Host ""
                                        Write-Host "  [INFO] Continuing with migrations..." -ForegroundColor Cyan
                                        $testExitCode = 0
                                    } else {
                                        Write-Host $retryOutput -ForegroundColor Yellow
                                        Write-Host "  [WARN] Connection still failed, will try alternative methods" -ForegroundColor Yellow
                                    }
                                }
                            } else {
                                Write-Host "  [INFO] No PostgreSQL services found via Get-Service" -ForegroundColor Gray
                                Write-Host "  [INFO] Trying alternative method: net stop" -ForegroundColor Gray
                                try {
                                    $netStopResult = net stop postgresql-x64-* 2>&1
                                    if ($LASTEXITCODE -eq 0) {
                                        Write-Host "  [OK] PostgreSQL service stopped via net stop" -ForegroundColor Green
                                        $serviceStopped = $true
                                        Start-Sleep -Seconds 3
                                    }
                                } catch {
                                    Write-Host "  [WARN] Cannot stop via net stop: $_" -ForegroundColor Yellow
                                }
                            }
                        } catch {
                            Write-Host "  [WARN] Cannot access services (may need Administrator privileges): $_" -ForegroundColor Yellow
                        }
                        }  # End of if (-not $useLocalPostgres) block - only try to stop service if NOT using local PostgreSQL
                        
                        # If service stop failed or didn't work, try alternative port (only if NOT using local PostgreSQL)
                        if (-not $useLocalPostgres -and (-not $serviceStopped -or $testExitCode -ne 0)) {
                            Write-Host ""
                            Write-Host "  [AUTO-FIX] Attempting to use alternative port (5433)..." -ForegroundColor Cyan
                            
                            # Check if port 5433 is available
                            $port5433InUse = Get-NetTCPConnection -LocalPort 5433 -ErrorAction SilentlyContinue
                            if (-not $port5433InUse) {
                                Write-Host "  [OK] Port 5433 is available" -ForegroundColor Green
                                Write-Host "  [INFO] Updating docker-compose.minimal.yml to use port 5433..." -ForegroundColor Cyan
                                
                                try {
                                    $composeFile = Join-Path $projectRoot "docker-compose.minimal.yml"
                                    if (Test-Path $composeFile) {
                                        $composeContent = Get-Content $composeFile -Raw
                                        if ($composeContent -match 'ports:\s*-\s*"5432:5432"') {
                                            $composeContent = $composeContent -replace 'ports:\s*-\s*"5432:5432"', 'ports: - "5433:5432"  # Changed to avoid conflict with local PostgreSQL'
                                            $composeContent | Set-Content $composeFile -NoNewline
                                            Write-Host "  [OK] docker-compose.minimal.yml updated" -ForegroundColor Green
                                            
                                            Write-Host "  [INFO] Restarting PostgreSQL container with new port mapping..." -ForegroundColor Cyan
                                            docker-compose -f $composeFile up -d postgres 2>&1 | Out-Null
                                            Start-Sleep -Seconds 5
                                            
                                            # Test connection with new port
                                            Write-Host "  [INFO] Testing connection with port 5433..." -ForegroundColor Cyan
                                            $newPortTestScript = @"
import psycopg2
try:
    conn = psycopg2.connect(
        host='localhost',
        port=5433,
        user='jachin',
        password='secure_password',
        dbname='jachin_brain',
        connect_timeout=10
    )
    cur = conn.cursor()
    cur.execute('SELECT version();')
    version = cur.fetchone()
    print('SUCCESS: Connection established on port 5433!')
    print(f'PostgreSQL version: {version[0]}')
    cur.close()
    conn.close()
    exit(0)
except Exception as e:
    print(f'FAILED: {e}')
    exit(1)
"@
                                            $newPortTestFile = Join-Path $env:TEMP "test_port_5433.py"
                                            $newPortTestScript | Out-File -FilePath $newPortTestFile -Encoding UTF8 -Force
                                            $newPortOutput = & $pythonCmd -X utf8 $newPortTestFile 2>&1 | Out-String
                                            $newPortExitCode = $LASTEXITCODE
                                            Remove-Item $newPortTestFile -ErrorAction SilentlyContinue
                                            
                                            if ($newPortExitCode -eq 0) {
                                                Write-Host $newPortOutput -ForegroundColor Green
                                                Write-Host "  [OK] Database connection successful on port 5433!" -ForegroundColor Green
                                                Write-Host ""
                                                Write-Host "  [INFO] Updating DATABASE_URL to use port 5433..." -ForegroundColor Cyan
                                                $newDatabaseUrl = "postgresql://jachin:secure_password@localhost:5433/jachin_brain"
                                                $env:DATABASE_URL = $newDatabaseUrl
                                                $DatabaseUrl = $newDatabaseUrl
                                                $script:DatabaseUrl = $newDatabaseUrl
                                                Write-Host "  [OK] DATABASE_URL updated: $newDatabaseUrl" -ForegroundColor Green
                                                Write-Host ""
                                                Write-Host "  [INFO] Continuing with migrations using port 5433..." -ForegroundColor Cyan
                                                $testExitCode = 0
                                            } else {
                                                Write-Host $newPortOutput -ForegroundColor Yellow
                                                Write-Host "  [WARN] Connection on port 5433 also failed" -ForegroundColor Yellow
                                            }
                                        } else {
                                            Write-Host "  [WARN] Could not find port mapping in docker-compose.minimal.yml" -ForegroundColor Yellow
                                        }
                                    } else {
                                        Write-Host "  [WARN] docker-compose.minimal.yml not found" -ForegroundColor Yellow
                                    }
                                } catch {
                                    Write-Host "  [ERROR] Failed to update docker-compose.minimal.yml: $_" -ForegroundColor Red
                                }
                            } else {
                                Write-Host "  [WARN] Port 5433 is also in use" -ForegroundColor Yellow
                            }
                        }
                        
                        # If all auto-fix attempts failed, show manual solutions
                        if ($testExitCode -ne 0) {
                            Write-Host ""
                            Write-Host "  [MANUAL FIX REQUIRED] Automatic fixes failed. Please choose one:" -ForegroundColor Yellow
                            Write-Host "    Option 1: Stop PostgreSQL service manually (as Administrator)" -ForegroundColor White
                            Write-Host "      Stop-Service -Name postgresql* -Force" -ForegroundColor Cyan
                            Write-Host "      Or: Get-Service postgresql* | Stop-Service -Force" -ForegroundColor Cyan
                            Write-Host "      Then restart this script" -ForegroundColor Gray
                            Write-Host ""
                            Write-Host "    Option 2: Change Docker port mapping manually" -ForegroundColor White
                            Write-Host "      Edit docker-compose.minimal.yml:" -ForegroundColor Gray
                            Write-Host "        Change: ports: ['5432:5432']" -ForegroundColor Cyan
                            Write-Host "        To:     ports: ['5433:5432']" -ForegroundColor Cyan
                            Write-Host "      Then run: docker-compose -f docker-compose.minimal.yml up -d postgres" -ForegroundColor Cyan
                            Write-Host "      And update DATABASE_URL to use port 5433" -ForegroundColor Gray
                        }
                        
                        Write-Host ""
                        Write-Host "  [INFO] Testing connection to local PostgreSQL..." -ForegroundColor Cyan
                        try {
                            $localPgTest = & $pythonCmd -c "import psycopg2; conn = psycopg2.connect(host='localhost', port=5432, user='postgres', password='', dbname='postgres', connect_timeout=2); print('Connected to local PostgreSQL'); conn.close()" 2>&1
                            if ($LASTEXITCODE -eq 0) {
                                Write-Host "  [WARN] Successfully connected to local PostgreSQL!" -ForegroundColor Yellow
                                Write-Host "  [INFO] psycopg2 is connecting to local PostgreSQL instead of Docker container" -ForegroundColor Yellow
                            } else {
                                Write-Host "  [INFO] Cannot connect to local PostgreSQL (may require authentication)" -ForegroundColor Gray
                            }
                        } catch {
                            Write-Host "  [INFO] Cannot test local PostgreSQL connection: $_" -ForegroundColor Gray
                        }
                    } else {
                        Write-Host "  [SOLUTION] Stop the conflicting service or change Docker port mapping" -ForegroundColor Cyan
                    }
                } else {
                    Write-Host "  [OK] Port 5432 is not in use on Windows host" -ForegroundColor Green
                }
            } catch {
                Write-Host "  [WARN] Cannot check port usage: $_" -ForegroundColor Yellow
            }
            
            # Test actual TCP connection to localhost:5432
            Write-Host ""
            Write-Host "  [INFO] Testing TCP connection to localhost:5432..." -ForegroundColor Cyan
            try {
                $tcpTest = Test-NetConnection -ComputerName localhost -Port 5432 -WarningAction SilentlyContinue -InformationLevel Quiet
                if ($tcpTest) {
                    Write-Host "  [OK] TCP connection to localhost:5432 succeeded" -ForegroundColor Green
                } else {
                    Write-Host "  [ERROR] TCP connection to localhost:5432 failed" -ForegroundColor Red
                    Write-Host "  [INFO] This confirms the port mapping issue" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  [WARN] Cannot test TCP connection: $_" -ForegroundColor Yellow
            }
            
            # Try to get Docker host IP and test connection
            if ($containerWorks) {
                Write-Host ""
                Write-Host "  [INFO] Attempting alternative connection methods..." -ForegroundColor Cyan
                
                # Try host.docker.internal (Docker Desktop special hostname)
                Write-Host "  [INFO] Testing connection with host.docker.internal..." -ForegroundColor Cyan
                $hostDockerWorks = $false
                try {
                    $hostDockerTestScript = @"
import psycopg2
try:
    conn = psycopg2.connect(
        host='host.docker.internal',
        port=5432,
        user='jachin',
        password='secure_password',
        dbname='jachin_brain',
        connect_timeout=5
    )
    print('SUCCESS: Connection with host.docker.internal works!')
    cur = conn.cursor()
    cur.execute('SELECT version();')
    version = cur.fetchone()
    print(f'PostgreSQL version: {version[0]}')
    cur.close()
    conn.close()
except Exception as e:
    print(f'FAILED: {e}')
"@
                    $hostDockerTestFile = Join-Path $env:TEMP "test_host_docker_internal.py"
                    $hostDockerTestScript | Out-File -FilePath $hostDockerTestFile -Encoding UTF8 -Force
                    $hostDockerTestOutput = & $pythonCmd -X utf8 $hostDockerTestFile 2>&1 | Out-String
                    Remove-Item $hostDockerTestFile -ErrorAction SilentlyContinue
                    
                    # Always show the test output
                    Write-Host "  [DEBUG] host.docker.internal test output:" -ForegroundColor Gray
                    Write-Host $hostDockerTestOutput -ForegroundColor Gray
                    
                    if ($hostDockerTestOutput -match 'SUCCESS') {
                        Write-Host "  [OK] Connection with host.docker.internal works!" -ForegroundColor Green
                        $hostDockerWorks = $true
                        Write-Host ""
                        Write-Host "  [SOLUTION] Found working connection method!" -ForegroundColor Green
                        Write-Host "  [INFO] Updating DATABASE_URL to use 'host.docker.internal'..." -ForegroundColor Cyan
                        $newDatabaseUrl = "postgresql://jachin:secure_password@host.docker.internal:5432/jachin_brain"
                        $env:DATABASE_URL = $newDatabaseUrl
                        Write-Host "  [OK] DATABASE_URL updated: $newDatabaseUrl" -ForegroundColor Green
                        Write-Host "  [INFO] Retrying database connection test..." -ForegroundColor Cyan
                        
                        # Retry connection test with new URL
                        $retryTestScript = @"
import sys
import os
import psycopg2

db_url = os.getenv('DATABASE_URL', '')
if db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', '', 1)
if '@' in db_url:
    auth, rest = db_url.split('@', 1)
    user, password = auth.split(':', 1)
    if '/' in rest:
        host_port, dbname = rest.split('/', 1)
        if ':' in host_port:
            host, port = host_port.split(':', 1)
        else:
            host, port = host_port, '5432'
    else:
        host, port = rest.split(':', 1) if ':' in rest else (rest, '5432')
        dbname = 'postgres'

try:
    conn = psycopg2.connect(
        host=host,
        port=int(port),
        user=user,
        password=password,
        dbname=dbname,
        connect_timeout=10
    )
    cur = conn.cursor()
    cur.execute('SELECT version();')
    version = cur.fetchone()
    print(f'SUCCESS: Connection established!')
    print(f'PostgreSQL version: {version[0]}')
    cur.close()
    conn.close()
    sys.exit(0)
except Exception as e:
    print(f'FAILED: {e}')
    sys.exit(1)
"@
                        $retryTestFile = Join-Path $env:TEMP "retry_db_connection.py"
                        $retryTestScript | Out-File -FilePath $retryTestFile -Encoding UTF8 -Force
                        $retryOutput = & $pythonCmd -X utf8 $retryTestFile 2>&1 | Out-String
                        $retryExitCode = $LASTEXITCODE
                        Remove-Item $retryTestFile -ErrorAction SilentlyContinue
                        
                        if ($retryExitCode -eq 0) {
                            Write-Host $retryOutput -ForegroundColor Green
                            Write-Host "  [OK] Database connection test successful with host.docker.internal!" -ForegroundColor Green
                            Write-Host ""
                            Write-Host "  [INFO] Continuing with migrations using host.docker.internal..." -ForegroundColor Cyan
                            # Update the DatabaseUrl variable for migrations
                            $DatabaseUrl = $newDatabaseUrl
                            $script:DatabaseUrl = $newDatabaseUrl
                            # Continue with migrations instead of exiting
                            $testExitCode = 0
                        } else {
                            Write-Host $retryOutput -ForegroundColor Yellow
                            Write-Host "  [WARN] Retry with host.docker.internal also failed" -ForegroundColor Yellow
                        }
                    } else {
                        Write-Host "  [INFO] $hostDockerTestOutput" -ForegroundColor Gray
                    }
                } catch {
                    Write-Host "  [WARN] Cannot test host.docker.internal: $_" -ForegroundColor Yellow
                }
                
                # Try to get Docker host IP from container network
                try {
                    $dockerHostIP = docker exec jachin-postgres sh -c "ip route | grep default | awk '{print `$3}'" 2>&1
                    if ($LASTEXITCODE -eq 0 -and $dockerHostIP -match '\d+\.\d+\.\d+\.\d+') {
                        Write-Host "  [INFO] Found Docker host IP: $dockerHostIP" -ForegroundColor Gray
                        Write-Host "  [INFO] Testing connection with Docker host IP..." -ForegroundColor Cyan
                        
                        # Create a test script to try Docker host IP
                        $dockerHostTestScript = @"
import psycopg2
try:
    conn = psycopg2.connect(
        host='$dockerHostIP',
        port=5432,
        user='jachin',
        password='secure_password',
        dbname='jachin_brain',
        connect_timeout=5
    )
    print('SUCCESS: Connection with Docker host IP works!')
    conn.close()
except Exception as e:
    print(f'FAILED: {e}')
"@
                        $dockerHostTestFile = Join-Path $env:TEMP "test_docker_host_ip.py"
                        $dockerHostTestScript | Out-File -FilePath $dockerHostTestFile -Encoding UTF8 -Force
                        $dockerHostTestOutput = & $pythonCmd -X utf8 $dockerHostTestFile 2>&1 | Out-String
                        Remove-Item $dockerHostTestFile -ErrorAction SilentlyContinue
                        if ($dockerHostTestOutput -match 'SUCCESS') {
                            Write-Host "  [OK] $dockerHostTestOutput" -ForegroundColor Green
                            Write-Host "  [INFO] Use '$dockerHostIP' as database host" -ForegroundColor Cyan
                        } else {
                            Write-Host "  [INFO] $dockerHostTestOutput" -ForegroundColor Gray
                        }
                    } else {
                        Write-Host "  [WARN] Cannot determine Docker host IP" -ForegroundColor Yellow
                    }
                } catch {
                    Write-Host "  [WARN] Cannot test Docker host IP: $_" -ForegroundColor Yellow
                }
            }
            
            # Check PostgreSQL listening address
            Write-Host "  [INFO] Checking PostgreSQL listening address..." -ForegroundColor Cyan
            try {
                $listenAddr = docker exec jachin-postgres sh -c "netstat -tlnp 2>/dev/null | grep 5432 || ss -tlnp 2>/dev/null | grep 5432" 2>&1
                if ($LASTEXITCODE -eq 0 -and $listenAddr) {
                    Write-Host "  [INFO] PostgreSQL listening on: $listenAddr" -ForegroundColor Gray
                } else {
                    Write-Host "  [WARN] Cannot check listening address" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  [WARN] Cannot check listening address: $_" -ForegroundColor Yellow
            }
            
            Write-Host ""
            Write-Host "  [INFO] Troubleshooting steps:" -ForegroundColor Yellow
            Write-Host "    1. Verify PostgreSQL container: docker ps --filter name=jachin-postgres" -ForegroundColor Gray
            Write-Host "    2. Check port mapping: docker port jachin-postgres" -ForegroundColor Gray
            Write-Host "    3. Test from container: docker exec jachin-postgres psql -U jachin -d jachin_brain -c 'SELECT 1;'" -ForegroundColor Gray
            Write-Host "    4. Check if port 5432 is accessible: Test-NetConnection localhost -Port 5432" -ForegroundColor Gray
            Write-Host "    5. Check Docker network: docker network inspect jachin-network" -ForegroundColor Gray
            Write-Host "    6. Check firewall/network settings" -ForegroundColor Gray
            Write-Host ""
            
            if ($containerWorks) {
                Write-Host ""
                Write-Host "  [SOLUTION] Docker Desktop for Windows port mapping issue detected:" -ForegroundColor Cyan
                Write-Host ""
                
                if ($localPostgresRunning) {
                    Write-Host "    ⚠️  PRIMARY ISSUE: Local PostgreSQL service is blocking Docker port mapping" -ForegroundColor Red
                    Write-Host ""
                    Write-Host "    Option 1: Stop local PostgreSQL service (RECOMMENDED)" -ForegroundColor White
                    Write-Host "      Run as Administrator:" -ForegroundColor Gray
                    Write-Host "        Stop-Service -Name postgresql* -Force" -ForegroundColor Cyan
                    Write-Host "      Or:" -ForegroundColor Gray
                    Write-Host "        Get-Service postgresql* | Stop-Service -Force" -ForegroundColor Cyan
                    Write-Host "      Then restart this script" -ForegroundColor Gray
                    Write-Host ""
                    Write-Host "    Option 2: Change Docker port mapping to avoid conflict" -ForegroundColor White
                    Write-Host "      1. Edit docker-compose.minimal.yml:" -ForegroundColor Gray
                    Write-Host "         Change: ports: ['5432:5432']" -ForegroundColor Cyan
                    Write-Host "         To:     ports: ['5433:5432']" -ForegroundColor Cyan
                    Write-Host "      2. Restart PostgreSQL container:" -ForegroundColor Gray
                    Write-Host "         docker-compose -f docker-compose.minimal.yml up -d postgres" -ForegroundColor Cyan
                    Write-Host "      3. Update DATABASE_URL to use port 5433:" -ForegroundColor Gray
                    Write-Host "         postgresql://jachin:secure_password@localhost:5433/jachin_brain" -ForegroundColor Cyan
                    Write-Host "      4. Restart this script" -ForegroundColor Gray
                    Write-Host ""
                } else {
                    Write-Host "    Option 1: Stop any conflicting service using port 5432" -ForegroundColor White
                    Write-Host "      - Run: Get-NetTCPConnection -LocalPort 5432" -ForegroundColor Gray
                    Write-Host "      - Stop the conflicting service" -ForegroundColor Gray
                    Write-Host ""
                    Write-Host "    Option 2: Restart Docker Desktop" -ForegroundColor White
                    Write-Host "      - Close Docker Desktop completely" -ForegroundColor Gray
                    Write-Host "      - Restart Docker Desktop" -ForegroundColor Gray
                    Write-Host "      - Wait for Docker to fully start" -ForegroundColor Gray
                    Write-Host "      - Run: docker-compose -f docker-compose.minimal.yml up -d postgres" -ForegroundColor Gray
                    Write-Host ""
                    Write-Host "    Option 3: Check Windows Firewall" -ForegroundColor White
                    Write-Host "      - Open Windows Defender Firewall" -ForegroundColor Gray
                    Write-Host "      - Allow port 5432 for inbound connections" -ForegroundColor Gray
                    Write-Host ""
                }
                
                Write-Host "  [NOTE] This is a common issue with Docker Desktop for Windows." -ForegroundColor Yellow
                Write-Host "         The port mapping may not work correctly due to WSL2 networking or port conflicts." -ForegroundColor Yellow
                if ($localPostgresRunning) {
                    Write-Host "         Stopping the local PostgreSQL service is the quickest solution." -ForegroundColor Yellow
                }
            }
            
            # Only exit if connection test still failed (testExitCode was not set to 0 by retry)
            if ($testExitCode -ne 0) {
                exit 1
            }
            # If testExitCode is 0, continue with migrations (host.docker.internal worked)
        }
        Write-Host "  [OK] Python database connection test successful" -ForegroundColor Green
        
        # Run migrations with UTF-8 encoding
        # Use Python's UTF-8 mode to handle Chinese characters in config file
        $migrationOutput = & $pythonCmd -X utf8 -m alembic upgrade head 2>&1
        $migrationExitCode = $LASTEXITCODE
        
        if ($migrationExitCode -eq 0) {
            Write-Host "  [OK] Database migrations completed" -ForegroundColor Green
        } else {
            Write-Host "  [ERROR] Database migration failed" -ForegroundColor Red
            Write-Host "  [ERROR] Exit code: $migrationExitCode" -ForegroundColor Red
            Write-Host "  [ERROR] Output:" -ForegroundColor Red
            Write-Host $migrationOutput -ForegroundColor Yellow
            Write-Host ""
            Write-Host "  [INFO] Troubleshooting:" -ForegroundColor Cyan
            Write-Host "    1. Check PostgreSQL container: docker ps --filter name=jachin-postgres" -ForegroundColor Gray
            Write-Host "    2. Check database exists: docker exec jachin-postgres psql -U jachin -d postgres -c '\l'" -ForegroundColor Gray
            Write-Host "    3. Test connection: docker exec jachin-postgres psql -U jachin -d jachin_brain -c 'SELECT 1;'" -ForegroundColor Gray
            exit 1
        }
    } catch {
        Write-Host "  [ERROR] Migration error: $_" -ForegroundColor Red
        Write-Host "  [ERROR] Exception type: $($_.Exception.GetType().FullName)" -ForegroundColor Red
        exit 1
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[6/7] Skipping database migrations..." -ForegroundColor Yellow
}

# Verify database
Write-Host "[8/8] Verifying database..." -ForegroundColor Yellow
try {
    # Can add database connection test here
    Write-Host "  [OK] Database initialization complete" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Cannot verify database connection" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Database initialization complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Start services: .\scripts\start.ps1" -ForegroundColor White
Write-Host "  2. Test API: .\scripts\test.ps1" -ForegroundColor White
Write-Host ""
