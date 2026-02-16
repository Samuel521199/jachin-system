# Check API Key configuration script
# 检查 API Key 配置脚本

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Checking API Key Configuration" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Check Windows environment variable
Write-Host "1. Checking Windows environment variable QWEN_AI_API_KEY..." -ForegroundColor Yellow
$winEnvVar = [System.Environment]::GetEnvironmentVariable("QWEN_AI_API_KEY", "User")
if (-not $winEnvVar) {
    $winEnvVar = [System.Environment]::GetEnvironmentVariable("QWEN_AI_API_KEY", "Machine")
}
if (-not $winEnvVar) {
    $winEnvVar = $env:QWEN_AI_API_KEY
}

if ($winEnvVar) {
    $maskedKey = if ($winEnvVar.Length -gt 8) {
        $winEnvVar.Substring(0, 4) + "..." + $winEnvVar.Substring($winEnvVar.Length - 4)
    } else {
        "***"
    }
    Write-Host "   [OK] Found: $maskedKey" -ForegroundColor Green
} else {
    Write-Host "   [NOT FOUND] QWEN_AI_API_KEY not set in Windows environment" -ForegroundColor Red
}

# Check .env file
Write-Host ""
Write-Host "2. Checking .env file..." -ForegroundColor Yellow
$envFile = Join-Path $ProjectRoot ".env"
if (Test-Path $envFile) {
    $envContent = Get-Content $envFile | Select-String -Pattern "QWEN_API_KEY"
    if ($envContent) {
        $envValue = ($envContent -split "=")[1].Trim()
        if ($envValue) {
            $maskedKey = if ($envValue.Length -gt 8) {
                $envValue.Substring(0, 4) + "..." + $envValue.Substring($envValue.Length - 4)
            } else {
                "***"
            }
            Write-Host "   [OK] Found in .env: $maskedKey" -ForegroundColor Green
        } else {
            Write-Host "   [INFO] QWEN_API_KEY is empty in .env (will use Windows env var if available)" -ForegroundColor Cyan
        }
    } else {
        Write-Host "   [INFO] QWEN_API_KEY not found in .env" -ForegroundColor Cyan
    }
} else {
    Write-Host "   [WARN] .env file not found" -ForegroundColor Yellow
}

# Check other environment variables
Write-Host ""
Write-Host "3. Checking other environment variables..." -ForegroundColor Yellow
$otherVars = @("QWEN_API_KEY", "DASHSCOPE_API_KEY")
$found = $false
foreach ($var in $otherVars) {
    $value = [System.Environment]::GetEnvironmentVariable($var, "User")
    if (-not $value) {
        $value = [System.Environment]::GetEnvironmentVariable($var, "Machine")
    }
    if (-not $value) {
        $value = (Get-Item "Env:$var" -ErrorAction SilentlyContinue).Value
    }
    if ($value) {
        $maskedKey = if ($value.Length -gt 8) {
            $value.Substring(0, 4) + "..." + $value.Substring($value.Length - 4)
        } else {
            "***"
        }
        Write-Host "   [OK] Found $var : $maskedKey" -ForegroundColor Green
        $found = $true
    }
}
if (-not $found) {
    Write-Host "   [INFO] No other API key environment variables found" -ForegroundColor Cyan
}

# Summary
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Summary" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

if ($winEnvVar) {
    Write-Host "[SUCCESS] Windows environment variable QWEN_AI_API_KEY is set" -ForegroundColor Green
    Write-Host "          The application will use this API key automatically." -ForegroundColor Green
} elseif ($found) {
    Write-Host "[SUCCESS] API key found in other environment variables" -ForegroundColor Green
} else {
    Write-Host "[WARNING] No API key found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "To set Windows environment variable:" -ForegroundColor Yellow
    Write-Host "  1. Open System Properties > Environment Variables" -ForegroundColor Yellow
    Write-Host "  2. Add new User variable:" -ForegroundColor Yellow
    Write-Host "     Name:  QWEN_AI_API_KEY" -ForegroundColor Yellow
    Write-Host "     Value: your-api-key-here" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Or edit .env file and set:" -ForegroundColor Yellow
    Write-Host "     QWEN_API_KEY=your-api-key-here" -ForegroundColor Yellow
}

Write-Host ""
