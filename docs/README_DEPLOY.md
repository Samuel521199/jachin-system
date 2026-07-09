# Packaged L3 Deployment

`dist_jachin_desktop` is the portable packaged L3 output. It should contain the
desktop executable, sidecars, runtime, minimal config examples, and logs
directory. Business skills, business MCPs, and optional models are installed
from L1 after startup.

## Start

```powershell
cd D:\Projects\jachi\jachin-system-main
.\scripts\start-layer3.ps1
```

Choose packaged mode. The script must use configuration inside
`dist_jachin_desktop`, not the development `.env`.

## Configure L1

The default L1 can be set in the console and saved as an L1 profile. A packaged
L3 can switch between L1 profiles. Each profile has an isolated installed
package source so private, test, and production L1 servers do not overwrite one
another accidentally.

Typical profile:

```text
http://47.86.39.173:3000
```

## Install Capabilities

Open Capability Install Center and refresh the catalog. Packaged mode should
show only installed business pages and core product pages. Installing a business
skill also installs declared MCP/model dependencies.

## Local Data

User-local state lives under `~/.jachin`, including:

- L1 profiles
- installed capability registry
- installed skill/MCP/model packages
- skill configuration written from package manifests
- Cognitive Kernel ledger

The packaged folder can be replaced during upgrades without deleting user
learning history or capability configuration.

## Health Checks

```powershell
Invoke-RestMethod http://127.0.0.1:18991/api/health
```

Check logs in:

```text
dist_jachin_desktop/logs/l3_debug.log
```
