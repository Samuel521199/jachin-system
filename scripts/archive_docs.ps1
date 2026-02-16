# Archive outdated documentation files
# 归档过时的文档文件

$archiveDir = Join-Path $PSScriptRoot "..\docs\archive"
$docsDir = Join-Path $PSScriptRoot "..\docs"

# Ensure archive directory exists
if (-not (Test-Path $archiveDir)) {
    New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null
}

# Files to archive
$filesToArchive = @(
    "V3.2_RESTRUCTURE_FINAL.md",
    "RESTRUCTURE_COMPLETE.md",
    "RESTRUCTURE_STATUS.md",
    "FINAL_RESTRUCTURE_REPORT.md",
    "STRUCTURE_COMPLETE.md",
    "STRUCTURE_FINAL_STATUS.md",
    "NEXT_STEPS.md",
    "NEXT_STEPS_AFTER_STARTUP.md",
    "NEXT_STEPS_AFTER_MIDDLEWARE.md",
    "MISSING_STEPS_SUMMARY.md",
    "TAILSCALE_FIX.md",
    "TAILSCALE_CONFIGURATION.md",
    "whitepaper_v3.2.md",
    "CLEANUP_SUMMARY.md",
    "DELETION_VERIFICATION_REPORT.md",
    "IMPLEMENTATION_READINESS.md",
    "CODING_READINESS_CHECKLIST.md",
    "INFRASTRUCTURE_READY.md",
    "V3.2_IMPLEMENTATION_STATUS.md",
    "V3.2_IMPLEMENTATION_COMPLETE.md",
    "STEP1_JACHIN_LINK_STATUS.md",
    "DOCUMENTATION_CLEANUP_PLAN.md",
    "DOCUMENTATION_CLEANUP_REPORT.md",
    "DOCUMENTATION_MERGE_REPORT.md",
    "FINAL_CLEANUP_PLAN.md"
)

$moved = 0
foreach ($file in $filesToArchive) {
    $sourcePath = Join-Path $docsDir $file
    if (Test-Path $sourcePath) {
        $destPath = Join-Path $archiveDir $file
        Move-Item -Path $sourcePath -Destination $destPath -Force -ErrorAction SilentlyContinue
        $moved++
        Write-Host "Moved: $file" -ForegroundColor Green
    }
}

Write-Host "`nTotal files moved: $moved" -ForegroundColor Cyan
