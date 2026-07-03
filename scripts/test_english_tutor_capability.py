from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = ROOT / "l3_client" / "local_mcps" / "english_tutor_mcp"
SKILL_DIR = ROOT / "skills_repo" / "com.jachin.skill.english-learning-assistant"
OUT_DIR = ROOT / "output" / "l1_capability_packages"


def _load_package_api():
    sys.path.insert(0, str(ROOT))
    mod = importlib.import_module("scripts.package_l1_capability")
    return mod.package_capability


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _direct_tool_smoke() -> dict[str, Any]:
    sys.path.insert(0, str(MCP_DIR))
    from english_tutor import (  # type: ignore
        english_correct_sentence,
        english_explain_word,
        english_make_examples,
        english_quiz_check_answer,
        english_quiz_generate,
        english_translate_cn_en,
    )

    return {
        "correction": english_correct_sentence("I very like play basketball"),
        "translation": english_translate_cn_en("\u9879\u76ee"),
        "word": english_explain_word("progress"),
        "examples": english_make_examples("workflow", count=3),
        "quiz": english_quiz_generate("project", count=2),
        "quiz_check": english_quiz_check_answer(question_id="q_project_cn", answer="\u9879\u76ee"),
    }


def _package_all() -> dict[str, str]:
    package_capability = _load_package_api()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mcp_zip = package_capability(MCP_DIR, OUT_DIR)
    skill_zip = package_capability(SKILL_DIR, OUT_DIR)
    return {
        "mcp_zip": str(mcp_zip),
        "mcp_sha256": _sha256(mcp_zip),
        "skill_zip": str(skill_zip),
        "skill_sha256": _sha256(skill_zip),
    }


def _packaged_dynamic_smoke(mcp_zip: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "com.jachin.mcp.english-tutor"
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(mcp_zip, "r") as zf:
            zf.extractall(target)

        manifest = _read_json(target / "plugin.json")
        sys.path.insert(0, str(target))
        results: dict[str, Any] = {}
        for tool in manifest.get("tools", []):
            module = importlib.import_module(str(tool["module"]))
            fn = getattr(module, str(tool["function"]))
            if tool["id"] == "english_correct_sentence":
                results[tool["id"]] = fn(text="I very like play basketball")
            elif tool["id"] == "english_translate_cn_en":
                results[tool["id"]] = fn(text="\u4f1a\u8bae")
            elif tool["id"] == "english_explain_word":
                results[tool["id"]] = fn(word="deadline")
            elif tool["id"] == "english_make_examples":
                results[tool["id"]] = fn(topic_or_word="project", count=2)
            elif tool["id"] == "english_quiz_generate":
                results[tool["id"]] = fn(topic="workflow", count=2)
            elif tool["id"] == "english_quiz_check_answer":
                results[tool["id"]] = fn(question_id="q_project_cn", answer="\u9879\u76ee")
        return {
            "package_id": manifest.get("id"),
            "tool_count": len(manifest.get("tools", [])),
            "results": results,
        }


def _install_zip(zip_path: Path, kind: str, package_id: str) -> Path:
    home = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser()
    cache_root = home / ("l3_mcp_cache" if kind == "mcp" else "l3_skill_cache")
    final_dir = cache_root / package_id
    if final_dir.exists():
        shutil.rmtree(final_dir)
    final_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(final_dir)
    return final_dir


def _write_installed_registry(records: list[dict[str, Any]]) -> Path:
    home = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser()
    registry_path = home / "capabilities" / "installed.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    if registry_path.exists():
        registry = _read_json(registry_path)
    else:
        registry = {"packages": {}}
    packages = registry.setdefault("packages", {})
    installed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for rec in records:
        package_id = rec["id"]
        packages[package_id] = {
            "id": package_id,
            "name": rec["name"],
            "version": rec["version"],
            "kind": rec["kind"],
            "source": "local-smoke",
            "package_url": None,
            "package_sha256": rec["package_sha256"],
            "installed_path": rec["installed_path"],
            "installed_at": installed_at,
            "enabled": True,
        }
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return registry_path


def _install_local_packaged(mcp_zip: Path, skill_zip: Path) -> dict[str, Any]:
    mcp_manifest = _read_zip_json(mcp_zip, "plugin.json")
    skill_manifest = _read_zip_json(skill_zip, "plugin.json")
    mcp_dir = _install_zip(mcp_zip, "mcp", mcp_manifest["id"])
    skill_dir = _install_zip(skill_zip, "skill", skill_manifest["id"])
    registry_path = _write_installed_registry(
        [
            {
                "id": mcp_manifest["id"],
                "name": mcp_manifest.get("name", mcp_manifest["id"]),
                "version": mcp_manifest.get("version", "0.0.0"),
                "kind": "mcp",
                "package_sha256": _sha256(mcp_zip),
                "installed_path": str(mcp_dir),
            },
            {
                "id": skill_manifest["id"],
                "name": skill_manifest.get("name", skill_manifest["id"]),
                "version": skill_manifest.get("version", "0.0.0"),
                "kind": "skill",
                "package_sha256": _sha256(skill_zip),
                "installed_path": str(skill_dir),
            },
        ]
    )
    return {
        "mcp_installed_path": str(mcp_dir),
        "skill_installed_path": str(skill_dir),
        "registry_path": str(registry_path),
    }


def _read_zip_json(zip_path: Path, inner: str) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        with zf.open(inner) as fh:
            return json.loads(fh.read().decode("utf-8-sig"))


def _publish_to_l1(zip_paths: list[Path], base_url: str, token: str) -> list[dict[str, Any]]:
    try:
        import requests
    except Exception as exc:
        return [{"ok": False, "error": f"requests is not installed: {exc}"}]

    out = []
    url = base_url.rstrip("/") + "/api/v1/store/publish"
    for path in zip_paths:
        with path.open("rb") as fh:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                files={"package": (path.name, fh, "application/zip")},
                data={"visibility": "PRIVATE"},
                timeout=60,
            )
        item: dict[str, Any] = {"zip": str(path), "status_code": resp.status_code}
        try:
            item["response"] = resp.json()
        except Exception:
            item["response_text"] = resp.text[:1000]
        item["ok"] = 200 <= resp.status_code < 300
        out.append(item)
    return out


def _default_l1_token() -> str:
    token = os.environ.get("JACHIN_DEV_TOKEN", "").strip()
    if token:
        return token
    cfg = Path.home() / ".jachin" / "nexus_config.json"
    if cfg.exists():
        return str(_read_json(cfg).get("access_token") or "").strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the offline English Tutor MCP/Skill package.")
    parser.add_argument("--install-local-packaged", action="store_true", help="Extract packages into ~/.jachin L3 caches and update installed registry.")
    parser.add_argument("--publish-l1", action="store_true", help="Publish packages to L1. Publish MCP first, then Skill.")
    parser.add_argument("--l1-base-url", default=os.environ.get("JACHIN_L1_BASE_URL", "http://localhost:3000"))
    parser.add_argument("--l1-token", default=_default_l1_token())
    args = parser.parse_args()

    direct = _direct_tool_smoke()
    packaged = _package_all()
    mcp_zip = Path(packaged["mcp_zip"])
    skill_zip = Path(packaged["skill_zip"])
    dynamic = _packaged_dynamic_smoke(mcp_zip)

    result: dict[str, Any] = {
        "ok": True,
        "source_smoke": direct,
        "packages": packaged,
        "packaged_dynamic_smoke": dynamic,
    }

    if args.install_local_packaged:
        result["local_packaged_install"] = _install_local_packaged(mcp_zip, skill_zip)

    if args.publish_l1:
        if not args.l1_token:
            result["l1_publish"] = [{"ok": False, "error": "missing L1 token"}]
            result["ok"] = False
        else:
            result["l1_publish"] = _publish_to_l1([mcp_zip, skill_zip], args.l1_base_url, args.l1_token)
            result["ok"] = all(item.get("ok") for item in result["l1_publish"])

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
