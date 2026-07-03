# MCP / Skill Independence Boundary

Jachin L3 should boot with a small OS-assistant core and load business
capabilities as independent L1/L2-distributed packages.

## Runtime Layers

- Core runtime: `l3_node`, `core`, desktop shell, agent loop, evidence, router,
  and generic OS primitives.
- Core bundled skills: `skills_repo/_bundled/*`.
- Core optional MCP packages: generic MCP packages such as filesystem, git,
  fetch, sqlite, Playwright, SMTP, Tavily, and Office PowerPoint.
- Business packages: HR, BI, PMO, finance analyst, game/test/demo, customer or
  company workflow packages.

Business packages must not be required for L3 startup.

## Loading Rules

Default L3 behavior:

- Load core bundled skills only.
- Load packages already installed into `~/.jachin/l3_skill_cache` or
  `~/.jachin/l3_mcp_cache`.
- Load core optional MCP packages from the repo in development.
- Hide repo business packages unless explicitly enabled for development.

Development override:

- `JACHIN_DEV_LOAD_REPO_CAPABILITY_PACKAGES=1` loads non-business extension
  packages directly from the repo.
- `JACHIN_DEV_LOAD_BUSINESS_CAPABILITY_PACKAGES=1` loads business packages
  directly from the repo.
- `JACHIN_BUILD_WITH_BUSINESS_PACKAGES=1` builds legacy/dev sidecars that embed
  business imports. Default sidecar builds keep those imports out.

These flags are for local development only. Production/L3 standalone machines
should receive business capabilities through L1/L2 inventory sync.

## L1 Package Requirements

An independently publishable package should contain:

- `plugin.json` for MCP/plugin packages, or `manifest.yaml` / `SKILL.md` for
  declarative skills.
- `id`, `version`, description, runtime type, and tool/capability metadata.
- `stdio_server` for L3_LOCAL stdio MCP packages, or `tools[]` for Python module
  tool packages.
- Package-relative startup paths via `__MCP_PACKAGE_ROOT__`.
- User/runtime state under `__JACHIN_HOME__` or `__JACHIN_WORKSPACE__`.

Do not use `__PROJECT_ROOT__` in business package runtime commands. A package
downloaded from L1 on another computer will not have the development repo path.

## Tools

Audit boundaries:

```powershell
python scripts\audit_capability_pack_boundaries.py --json output\capability_pack_audit.json
```

Create an L1-uploadable zip:

```powershell
python scripts\package_l1_capability.py skills_repo\plugin\com.jachin.hr.recruitment --out dist_l1_capabilities
```

The packaging script excludes `.env`, logs, caches, output folders, archives,
and local runtime data.

## Design Intent

The main process should know how to discover, rank, invoke, and verify
capabilities. It should not contain HR, BI, PMO, or customer workflow logic.
Those domains belong in packages with their own metadata, dependencies,
configuration, tests, and release lifecycle.
