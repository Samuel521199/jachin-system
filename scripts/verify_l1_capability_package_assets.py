"""Verify a Jachin L1 capability package contains declared offline assets."""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", help="Path to a .zip capability package")
    parser.add_argument("--require-prefix", action="append", default=[], help="Asset path prefix that must exist")
    parser.add_argument("--require-preserve", action="append", default=[], help="Preserved user-data path that must be declared")
    args = parser.parse_args()

    package = Path(args.package)
    if not package.is_file():
        print(f"Package not found: {package}", file=sys.stderr)
        return 2

    with zipfile.ZipFile(package) as zf:
        names = set(zf.namelist())
        if ".jachin-package.json" not in names:
            print("Missing .jachin-package.json", file=sys.stderr)
            return 1
        meta = json.loads(zf.read(".jachin-package.json").decode("utf-8-sig"))
        assets = meta.get("package_assets") or []
        preserve = meta.get("preserve_user_data") or []
        asset_targets = [str(item.get("to") or "") for item in assets if isinstance(item, dict)]

        missing_declared = [target for target in asset_targets if target not in names]
        if missing_declared:
            print("Declared assets missing from zip:", file=sys.stderr)
            for target in missing_declared:
                print(f"  - {target}", file=sys.stderr)
            return 1

        for prefix in args.require_prefix:
            if not any(name.startswith(prefix) for name in names):
                print(f"Missing required asset prefix: {prefix}", file=sys.stderr)
                return 1

        for required in args.require_preserve:
            if required not in preserve:
                print(f"Missing preserve_user_data declaration: {required}", file=sys.stderr)
                return 1

        print(f"OK package={package}")
        print(f"id={meta.get('id')} version={meta.get('version')} kind={meta.get('kind')}")
        print(f"assets={len(asset_targets)} preserve_user_data={len(preserve)}")
        for target in asset_targets:
            print(f"asset: {target}")
        for item in preserve:
            print(f"preserve: {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
