# 在仓库根目录执行 PMO BMO main_skill（避免在 scripts 下运行导致 ModuleNotFoundError: l3_node）
# 用法: .\scripts\run-pmo-bmo-skill.ps1 output-docs
#       .\scripts\run-pmo-bmo-skill.ps1 full --snapshot=2026-04-02
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot
python -m l3_node.skills.pmo_bmo.main_skill @args
exit $LASTEXITCODE
