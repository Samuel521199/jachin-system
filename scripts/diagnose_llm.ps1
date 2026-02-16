# Diagnose LLM Provider initialization issues
# 诊断 LLM Provider 初始化问题

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Diagnosing LLM Provider Initialization" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Check environment variables
Write-Host "1. Checking environment variables..." -ForegroundColor Yellow
$envVars = @("QWEN_AI_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")
$found = $false
foreach ($var in $envVars) {
    $value = [System.Environment]::GetEnvironmentVariable($var, "User")
    if (-not $value) {
        $value = [System.Environment]::GetEnvironmentVariable($var, "Machine")
    }
    if (-not $value) {
        $value = (Get-Item "Env:$var" -ErrorAction SilentlyContinue).Value
    }
    if ($value) {
        $masked = if ($value.Length -gt 8) {
            $value.Substring(0, 4) + "..." + $value.Substring($value.Length - 4)
        } else {
            "***"
        }
        Write-Host "   [OK] $var : $masked" -ForegroundColor Green
        $found = $true
    }
}
if (-not $found) {
    Write-Host "   [ERROR] No API key found in environment variables" -ForegroundColor Red
}

# Check Python environment
Write-Host ""
Write-Host "2. Checking Python environment..." -ForegroundColor Yellow
$pythonCheck = conda run -n jachin-dev python -c "import sys; print(sys.executable)" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "   [OK] Python environment: jachin-dev" -ForegroundColor Green
} else {
    Write-Host "   [ERROR] Python environment check failed" -ForegroundColor Red
}

# Check dashscope package
Write-Host ""
Write-Host "3. Checking dashscope package..." -ForegroundColor Yellow
$dashscopeCheck = conda run -n jachin-dev python -c "import dashscope; print('dashscope version:', dashscope.__version__)" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "   [OK] dashscope installed" -ForegroundColor Green
    Write-Host "   $dashscopeCheck" -ForegroundColor Gray
} else {
    Write-Host "   [ERROR] dashscope not installed" -ForegroundColor Red
    Write-Host "   Run: conda activate jachin-dev && pip install dashscope" -ForegroundColor Yellow
}

# Test LLM Provider initialization
Write-Host ""
Write-Host "4. Testing LLM Provider initialization..." -ForegroundColor Yellow
$testScript = @"
import os
import sys
sys.path.insert(0, r'$ProjectRoot\backend')

try:
    from core.llm.factory import LLMProviderFactory
    from config import settings
    
    print(f'Provider type: {settings.LLM_PROVIDER}')
    print(f'Model: {settings.LLM_MODEL}')
    
    # Check API Key
    api_key = (
        os.getenv('QWEN_API_KEY')
        or os.getenv('DASHSCOPE_API_KEY')
        or os.getenv('QWEN_AI_API_KEY')
    )
    
    if not api_key:
        print('ERROR: API Key not found')
        sys.exit(1)
    
    print(f'API Key found (length: {len(api_key)})')
    
    # Try to create provider
    provider = LLMProviderFactory.create_provider(
        provider_type=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL
    )
    
    print(f'SUCCESS: Provider created: {type(provider).__name__}')
    model_info = provider.get_model_info()
    print(f'Model info: {model_info}')
    
except Exception as e:
    print(f'ERROR: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
"@

$testScript | conda run -n jachin-dev python 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "   [OK] LLM Provider initialization successful" -ForegroundColor Green
} else {
    Write-Host "   [ERROR] LLM Provider initialization failed" -ForegroundColor Red
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Diagnosis complete" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
