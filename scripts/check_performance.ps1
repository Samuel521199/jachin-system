# Performance Monitoring Query Script
# Query performance metrics from Jachin-System backend

param(
    [Parameter(Mandatory=$false)]
    [string]$Endpoint = "stats",  # stats, metrics, errors, alerts
    
    [Parameter(Mandatory=$false)]
    [int]$Minutes = 5  # For metrics and errors
)

$ErrorActionPreference = "Stop"

$baseUrl = "http://localhost:18888/api/v3/monitoring"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Jachin-System Performance Monitor" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check backend service
Write-Host "Checking backend service..." -ForegroundColor Yellow
try {
    Invoke-RestMethod -Uri "http://localhost:18888/health" -Method Get -TimeoutSec 2 | Out-Null
    Write-Host "Backend service is running" -ForegroundColor Green
} catch {
    Write-Host "Backend service is not running" -ForegroundColor Red
    Write-Host "Please start backend first: .\scripts\start.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host ""

# Build URL
$url = "$baseUrl/$Endpoint"
if ($Endpoint -eq "metrics" -or $Endpoint -eq "errors") {
    $url += "?minutes=$Minutes"
}

Write-Host "Query URL: $url" -ForegroundColor Cyan
Write-Host ""

try {
    $response = Invoke-RestMethod -Uri $url -Method Get -ContentType "application/json"
    
    # Format output
    switch ($Endpoint) {
        "stats" {
            Write-Host "Performance Statistics:" -ForegroundColor Green
            Write-Host ""
            foreach ($metric in $response.PSObject.Properties) {
                $name = $metric.Name
                $data = $metric.Value
                Write-Host "  [$name]" -ForegroundColor Yellow
                Write-Host "    Count: $($data.count)" -ForegroundColor White
                Write-Host "    Avg Time: $([math]::Round($data.avg_time, 2))s" -ForegroundColor White
                Write-Host "    Min Time: $([math]::Round($data.min_time, 2))s" -ForegroundColor White
                Write-Host "    Max Time: $([math]::Round($data.max_time, 2))s" -ForegroundColor White
                Write-Host "    Errors: $($data.errors)" -ForegroundColor $(if ($data.errors -gt 0) { "Red" } else { "Green" })
                Write-Host "    Error Rate: $([math]::Round($data.error_rate * 100, 1))%" -ForegroundColor $(if ($data.error_rate -gt 0.1) { "Red" } else { "Green" })
                Write-Host ""
            }
        }
        "metrics" {
            Write-Host "Recent Performance Metrics (last $Minutes minutes):" -ForegroundColor Green
            Write-Host "Total: $($response.count)" -ForegroundColor Cyan
            Write-Host ""
            if ($response.metrics.Count -gt 0) {
                $response.metrics | ForEach-Object {
                    Write-Host "  [$($_.name)]" -ForegroundColor Yellow
                    Write-Host "    Value: $($_.value)s" -ForegroundColor White
                    Write-Host "    Timestamp: $($_.timestamp)" -ForegroundColor Gray
                    if ($_.tags) {
                        Write-Host "    Tags: $($_.tags | ConvertTo-Json -Compress)" -ForegroundColor Gray
                    }
                    Write-Host ""
                }
            } else {
                Write-Host "  No data available" -ForegroundColor Gray
            }
        }
        "errors" {
            Write-Host "Recent Errors (last $Minutes minutes):" -ForegroundColor Green
            Write-Host "Total: $($response.count)" -ForegroundColor Cyan
            Write-Host ""
            if ($response.errors.Count -gt 0) {
                $response.errors | ForEach-Object {
                    Write-Host "  [$($_.name)]" -ForegroundColor Red
                    Write-Host "    Duration: $($_.duration)s" -ForegroundColor White
                    Write-Host "    Timestamp: $($_.timestamp)" -ForegroundColor Gray
                    if ($_.tags) {
                        Write-Host "    Tags: $($_.tags | ConvertTo-Json -Compress)" -ForegroundColor Gray
                    }
                    Write-Host ""
                }
            } else {
                Write-Host "  No errors" -ForegroundColor Green
            }
        }
        "alerts" {
            Write-Host "Current Alerts:" -ForegroundColor Green
            Write-Host "Total: $($response.count)" -ForegroundColor Cyan
            Write-Host ""
            if ($response.alerts.Count -gt 0) {
                $response.alerts | ForEach-Object {
                    $color = if ($_.type -like "*error*") { "Red" } else { "Yellow" }
                    Write-Host "  [$($_.type)]" -ForegroundColor $color
                    Write-Host "    $($_.message)" -ForegroundColor White
                    Write-Host ""
                }
            } else {
                Write-Host "  No alerts" -ForegroundColor Green
            }
        }
        default {
            Write-Host "Response:" -ForegroundColor Green
            $response | ConvertTo-Json -Depth 10
        }
    }
    
} catch {
    Write-Host "Error: Query failed" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.Exception.Response) {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $responseBody = $reader.ReadToEnd()
        Write-Host "Response: $responseBody" -ForegroundColor Red
    }
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Query completed" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
