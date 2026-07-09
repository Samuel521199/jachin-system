# L3 Slim Distribution And Subscribed Artifacts

L3 is distributed as a slim desktop/runtime shell. Business capabilities are
not bundled into the product by default.

## Runtime Boundary

Bundled with L3:

- Cognitive Kernel
- Tool execution host
- core MCP/skill loader
- L1 profile/catalog/install center
- IM/channel infrastructure required by the product shell

Installed from L1:

- business skills
- business MCPs
- optional local models
- skill-specific configuration templates

## Install Boundary

Installed artifacts are reconciled by:

- `capability_id`
- `version`
- `source_profile`
- package checksum

The product treats `capability_id` as globally unique. The package store still
keeps source isolation as a safety net so a test L1 and a production L1 cannot
silently overwrite each other's package files.

## Packaged Mode Rule

Packaged mode must not scan repo-local business skill folders. It may load:

- core product capabilities
- installed artifacts under `~/.jachin`
- packaged runtime files under `dist_jachin_desktop`

If a business page such as PMO, BI, HR, game QA, or English learning appears in
packaged mode, it must be because the corresponding business skill is installed.

## Development Mode Rule

Development mode may scan repo-local skill and MCP packages so authors can test
before publishing to L1.
