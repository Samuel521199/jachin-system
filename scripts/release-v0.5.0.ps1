# v0.5.0 release: commit, tag, push
# Run after: git config --global user.email "your@email.com"
#            git config --global user.name "Your Name"

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))

$msg = @"
v0.5.0: Conda support, edge agent rename, battle A/B/C, script unification

- Layer2: Conda env (jachin-layer2, Python 3.11) for Ray compatibility; fallback to requirements-layer2.txt
- Rename: 机甲 -> 边缘智能体 across docs and code
- Battle A: pair CLI (core/cli.py); Battle B: bounty board migration; Battle C: neuron plaza UI
- Scripts: install-layer2/cloud, start-layer2/cloud, run-pair, check-prerequisites; one-click flow
- Pairing: integrated into install-layer2, skip if already paired
- Docs: ecosystem whitepaper, revenue/royalty spec, GTM strategy
"@

git commit -m $msg
if ($LASTEXITCODE -ne 0) { exit 1 }

git tag -a v0.5.0 -m "Release v0.5.0"
git push origin main
git push origin v0.5.0

Write-Host ""
Write-Host "[OK] v0.5.0 released and pushed to GitHub" -ForegroundColor Green
