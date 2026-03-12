# Fix .bat encoding: GBK + CRLF, no BOM (for Chinese Windows CMD)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\fix-bat-encoding.ps1

param([string[]]$Paths = @())

$ProjectRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$gbk = [System.Text.Encoding]::GetEncoding("GBK")

$batFiles = if ($Paths.Count -gt 0) {
    $Paths | ForEach-Object { Get-Item $_ -ErrorAction SilentlyContinue } | Where-Object { $_.Extension -eq ".bat" }
} else {
    Get-ChildItem -Path $ProjectRoot -Filter "*.bat" -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch "node_modules|\.git" }
}

foreach ($f in $batFiles) {
    try {
        $content = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)
        $content = $content -replace "`r?`n", "`r`n"
        $bytes = $gbk.GetBytes($content)
        [System.IO.File]::WriteAllBytes($f.FullName, $bytes)
        Write-Host "[OK] $($f.FullName.Replace($ProjectRoot, '.'))" -ForegroundColor Green
    } catch {
        Write-Host "[FAIL] $($f.FullName): $_" -ForegroundColor Red
    }
}
Write-Host "Done. Batch files: GBK + CRLF, no BOM."
