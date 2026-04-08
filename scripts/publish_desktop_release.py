#!/usr/bin/env python3
"""
一键：将 Tauri 构建产物上传 MinIO，并向 Nexus 登记 desktop_app_releases。

依赖:
  pip install boto3

环境变量（与 Nexus / jachin-downloads 的 DESKTOP_RELEASES_S3_* 对齐）:
  DESKTOP_RELEASES_S3_BUCKET
  DESKTOP_RELEASES_S3_ACCESS_KEY
  DESKTOP_RELEASES_S3_SECRET_KEY
  DESKTOP_RELEASES_S3_ENDPOINT       例: http://127.0.0.1:9000
  DESKTOP_RELEASES_S3_REGION         默认 us-east-1
  DESKTOP_RELEASES_S3_FORCE_PATH_STYLE  默认 true

  NEXUS_BASE_URL                     例: http://localhost:3000（无尾斜杠）
  NEXUS_ADMIN_SECRET                 与 NEXUS_ADMIN_SECRET / X-Admin-Token 一致

用法（在项目根目录）:
  python scripts/publish_desktop_release.py --sign
  python scripts/publish_desktop_release.py
  python scripts/publish_desktop_release.py --installer path/to/setup.exe --sig path/to/setup.exe.sig
  python scripts/publish_desktop_release.py --installer .../jachin-desktop.exe --unsigned
  python scripts/publish_desktop_release.py --dry-run
  python scripts/publish_desktop_release.py --notes "修复若干问题"

或在 clients/desktop: npm run publish-desktop-release

环境变量可写入 cloud/nexus/.env.local；脚本启动时会自动加载（不覆盖已在终端里 export 的值）。

说明:
  - 默认从 clients/desktop/src-tauri/target/release/bundle 下自动发现 .exe/.msi 与 .sig。
  - 签名文件内容（整文件文本）即入库的 Tauri updater signature。
  - 无 .sig 时用 --unsigned 写入占位签名（仅发行大厅人工下载；Tauri 热更新签名校验会失败）。
  - bundle.active=false 时若未生成安装包，请先用 --installer 指定路径。

========== 正式签名（热更新）==========
「签名」指 minisign 对「要分发的安装包文件」生成的 detached signature。客户端 tauri.conf.json
里的 plugins.updater.pubkey 必须与签名时使用的私钥成对；否则下载后校验失败。

1) 若仓库里已有 pubkey、且你持有对应私钥（.key 等）：
   在 clients/desktop 下对已发布的安装包签名（示例）：
     npx tauri signer sign -f <私钥文件路径> path\\to\\jachin-desktop.exe
   会生成同目录的 jachin-desktop.exe.sig（或 CLI 提示的路径）。勿改安装包内容后再签。

2) 若没有密钥对，需新建一套（会替换 pubkey，所有已安装客户端需用含新 pubkey 的包重装机一次）：
     npx tauri signer generate -w .\\tauri-desktop-updater.key
   按提示保存密码；终端会打印公钥字符串，写入 src-tauri/tauri.conf.json 的 plugins.updater.pubkey。
   然后用上一步 sign 对 .exe 签名。

3) 一键签名并发布（推荐）：私钥须与 tauri.conf.json 的 pubkey 成对；**不能**用 pubkey 签名。
   将私钥保存为 clients/desktop/tauri-desktop-updater.key（已 gitignore），或设环境变量 TAURI_PRIVATE_KEY_PATH：
     python scripts\\publish_desktop_release.py --sign
   或在 clients/desktop： npm run publish-desktop-release

4) 手动已有 .sig 时：
     python scripts\\publish_desktop_release.py
   或： python scripts\\publish_desktop_release.py --installer ... --sig ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DESKTOP_ROOT = ROOT / "clients" / "desktop"
TAURI_CONF = DESKTOP_ROOT / "src-tauri" / "tauri.conf.json"
VERSION_FILE = DESKTOP_ROOT / "VERSION"
DEFAULT_BUNDLE = DESKTOP_ROOT / "src-tauri" / "target" / "release" / "bundle"

# 与 --sign 默认查找路径一致（已加入 clients/desktop/.gitignore，勿提交）
DEFAULT_TAURI_UPDATER_KEY = DESKTOP_ROOT / "tauri-desktop-updater.key"

# 无真实 .sig 时占位；客户端 updater 不可用，仅满足 API 非空校验
SIGNATURE_UNSIGNED_PLACEHOLDER = "UNSIGNED_PLACEHOLDER_NOT_FOR_TAURI_UPDATER"


def try_load_nexus_dotenv() -> None:
    """从 cloud/nexus/.env.local、.env 解析 KEY=VALUE，不覆盖已存在的环境变量。"""
    for name in (".env.local", ".env"):
        path = ROOT / "cloud" / "nexus" / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or str(v).strip() == "":
        return default
    return v.strip()


def read_version_from_tauri_conf() -> str:
    if not TAURI_CONF.is_file():
        raise FileNotFoundError(f"未找到 {TAURI_CONF}")
    data = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    v = data.get("version")
    if not v or not isinstance(v, str):
        raise ValueError("tauri.conf.json 缺少 version")
    return v.strip()


def read_release_version() -> str:
    """优先 clients/desktop/VERSION（与 npm run sync-version 一致），否则读 tauri.conf.json。"""
    if VERSION_FILE.is_file():
        first = VERSION_FILE.read_text(encoding="utf-8").strip().splitlines()
        if first and first[0].strip():
            return first[0].strip()
    return read_version_from_tauri_conf()


def find_signature_beside(installer: Path) -> Path | None:
    """常见命名: foo.exe.sig / foo.exe.sig 同目录同名 .sig。"""
    same_stem = installer.with_name(installer.name + ".sig")
    if same_stem.is_file():
        return same_stem
    alt = installer.with_suffix(installer.suffix + ".sig")
    if alt.is_file():
        return alt
    # 同目录唯一 .sig
    sigs = list(installer.parent.glob("*.sig"))
    if len(sigs) == 1:
        return sigs[0]
    return None


def pick_windows_installer(bundle: Path) -> Path | None:
    """优先 NSIS / MSI 目录下体积最大的 .exe/.msi。"""
    candidates: list[Path] = []
    for sub in ("nsis", "msi", "wix"):
        d = bundle / sub
        if not d.is_dir():
            continue
        for ext in ("*.exe", "*.msi"):
            candidates.extend(d.glob(ext))
    if not candidates:
        # 兜底：bundle 下递归找安装包（排除 target/release 根目录杂文件）
        for p in bundle.rglob("*.exe"):
            if "bundle" in p.parts:
                candidates.append(p)
        for p in bundle.rglob("*.msi"):
            candidates.append(p)
    if not candidates:
        return None
    # 取最大文件（通常是主安装包）
    return max(candidates, key=lambda p: p.stat().st_size)


def pick_release_dir_exe() -> Path | None:
    """
    bundle.active=false 时常见：主程序在 target/release/*.exe，不在 bundle/nsis/。
    优先 jachin-desktop.exe，否则取 release 根目录下体积最大的 .exe。
    """
    rel = DESKTOP_ROOT / "src-tauri" / "target" / "release"
    if not rel.is_dir():
        return None
    preferred = rel / "jachin-desktop.exe"
    if preferred.is_file():
        return preferred
    exes = [p for p in rel.glob("*.exe") if p.is_file()]
    if not exes:
        return None
    return max(exes, key=lambda p: p.stat().st_size)


def discover_installer_and_sig(bundle: Path) -> tuple[Path, Path]:
    inst = pick_windows_installer(bundle)
    if inst is None:
        inst = pick_release_dir_exe()
    if inst is None:
        raise FileNotFoundError(
            f"在 {bundle} 与 target/release 下未发现 .exe/.msi。"
            "请先 tauri build 或使用 --installer 指定路径。"
        )
    sig = find_signature_beside(inst)
    if sig is None:
        raise FileNotFoundError(
            f"未找到与 {inst.name} 对应的 .sig（尝试过 {inst.name}.sig 及同目录唯一 *.sig）。"
            "请使用 --sig 指定。"
        )
    return inst, sig


def make_s3_client():
    try:
        import boto3  # type: ignore
        from botocore.client import Config  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "请先安装 boto3: pip install boto3\n" + str(e)
        ) from e

    bucket = _env("DESKTOP_RELEASES_S3_BUCKET")
    ak = _env("DESKTOP_RELEASES_S3_ACCESS_KEY")
    sk = _env("DESKTOP_RELEASES_S3_SECRET_KEY")
    if not bucket or not ak or not sk:
        raise SystemExit(
            "请设置 DESKTOP_RELEASES_S3_BUCKET / ACCESS_KEY / SECRET_KEY"
        )

    endpoint = _env("DESKTOP_RELEASES_S3_ENDPOINT") or None
    region = _env("DESKTOP_RELEASES_S3_REGION") or "us-east-1"
    force_path = (_env("DESKTOP_RELEASES_S3_FORCE_PATH_STYLE") or "true").lower() in (
        "1",
        "true",
        "yes",
    )

    session = boto3.session.Session()
    return session.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path" if force_path else "auto"}),
    ), bucket


def upload_file_s3(
    local_path: Path,
    object_key: str,
    dry_run: bool,
) -> None:
    if dry_run:
        print(f"[dry-run] PUT s3://<bucket>/{object_key} <= {local_path}")
        return
    client, bucket = make_s3_client()
    extra: dict[str, Any] = {}
    ct = _guess_content_type(local_path.name)
    if ct:
        extra["ContentType"] = ct
    print(f"上传 {local_path.name} -> s3://{bucket}/{object_key} ({local_path.stat().st_size} bytes) ...")
    client.upload_file(str(local_path), bucket, object_key, ExtraArgs=extra or None)


def _guess_content_type(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith(".exe"):
        return "application/vnd.microsoft.portable-executable"
    if lower.endswith(".msi"):
        return "application/octet-stream"
    return None


def read_signature_text(sig_path: Path) -> str:
    raw = sig_path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        raise ValueError(f"签名文件为空: {sig_path}")
    return raw


def post_nexus(payload: dict[str, Any], dry_run: bool) -> None:
    base = (_env("NEXUS_BASE_URL") or "").rstrip("/")
    secret = _env("NEXUS_ADMIN_SECRET") or ""
    if not base or not secret:
        raise SystemExit("请设置 NEXUS_BASE_URL 与 NEXUS_ADMIN_SECRET")

    url = f"{base}/api/v1/admin/desktop-releases"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if dry_run:
        print(f"[dry-run] POST {url}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Admin-Token": secret,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = resp.read().decode("utf-8", errors="replace")
            print(f"Nexus HTTP {resp.status}: {out}")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"Nexus HTTP {e.code}: {err}", file=sys.stderr)
        raise SystemExit(1) from e


def normalize_version(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("version 为空")
    # 允许带 v 前缀，入库可与 semver 工具一致
    return v


def resolve_installer_path(args: argparse.Namespace) -> Path:
    """与 --unsigned 相同的安装包发现逻辑（显式 --installer 或 bundle/release 自动发现）。"""
    if args.installer:
        installer = args.installer.resolve()
        if not installer.is_file():
            raise SystemExit(f"安装包不存在: {installer}")
        return installer
    bd = args.bundle_dir.resolve()
    inst = pick_windows_installer(bd)
    if inst is None:
        inst = pick_release_dir_exe()
    if inst is None:
        raise SystemExit(
            "未在 bundle 与 target/release 发现 .exe。请使用 --installer 指向安装包路径。"
        )
    return inst


def resolve_private_key_path(cli_path: Path | None) -> Path:
    """私钥路径：--private-key-path > TAURI_PRIVATE_KEY_PATH > 默认 tauri-desktop-updater.key。"""
    if cli_path is not None:
        p = cli_path.expanduser().resolve()
        if not p.is_file():
            raise SystemExit(f"私钥文件不存在: {p}")
        return p
    env_p = _env("TAURI_PRIVATE_KEY_PATH")
    if env_p:
        p = Path(env_p).expanduser().resolve()
        if not p.is_file():
            raise SystemExit(f"TAURI_PRIVATE_KEY_PATH 指向的文件不存在: {p}")
        return p
    default = DEFAULT_TAURI_UPDATER_KEY.resolve()
    if default.is_file():
        return default
    raise SystemExit(
        "未找到 updater 私钥。密钥不能手写，须用工具生成或与现有 pubkey 成对的文件。\n\n"
        "【首次使用 / 可换一套新密钥】在 clients/desktop 目录执行：\n"
        "  npx tauri signer generate -w tauri-desktop-updater.key\n"
        "按提示操作；终端会打印公钥字符串，粘贴到 src-tauri/tauri.conf.json 的 plugins.updater.pubkey，\n"
        "然后重新打包安装客户端（用户需装这版后，热更新才认新公钥）。\n\n"
        "【仓库里已有 pubkey】若公钥是同事生成的，向其索取对应私钥文件，再用：\n"
        "  --private-key-path <路径> 或 TAURI_PRIVATE_KEY_PATH 或保存为：\n"
        f"  {DEFAULT_TAURI_UPDATER_KEY}\n\n"
        "说明：公钥 pubkey 不能反推私钥，不能用于签名。"
    )


def run_tauri_sign_installer(
    installer: Path,
    private_key_path: Path,
    password: str | None,
    dry_run: bool,
) -> None:
    """在 clients/desktop 下执行 npx tauri signer sign（与 README 一致）。"""
    if dry_run:
        print(
            f"[dry-run] (cd clients/desktop && npx tauri signer sign -f {private_key_path} {installer})"
        )
        return
    cmd: list[str] = [
        "npx",
        "tauri",
        "signer",
        "sign",
        "-f",
        str(private_key_path.resolve()),
        str(installer.resolve()),
    ]
    if password:
        cmd.extend(["-p", password])
    print("运行:", " ".join(cmd[:6]), "... sign", installer.name, file=sys.stderr)
    r = subprocess.run(cmd, cwd=str(DESKTOP_ROOT))
    if r.returncode != 0:
        raise SystemExit("tauri signer sign 失败（见上输出）。请确认已 npm install 且私钥与 pubkey 成对。")


def main() -> None:
    try_load_nexus_dotenv()
    ap = argparse.ArgumentParser(description="发布桌面端到 MinIO 并登记 Nexus")
    ap.add_argument(
        "--bundle-dir",
        type=Path,
        default=DEFAULT_BUNDLE,
        help="Tauri bundle 根目录（默认 target/release/bundle）",
    )
    ap.add_argument("--installer", type=Path, help="安装包 .exe/.msi 路径（覆盖自动发现）")
    ap.add_argument("--sig", type=Path, help="Tauri .sig 路径（覆盖自动发现）")
    ap.add_argument(
        "--unsigned",
        action="store_true",
        help="无真实签名时使用占位符（仅人工下载；热更新不可用）",
    )
    ap.add_argument(
        "--sign",
        action="store_true",
        help="缺少 .sig 时自动调用 tauri signer sign（需私钥，与 pubkey 成对；见 --private-key-path / TAURI_PRIVATE_KEY_PATH / tauri-desktop-updater.key）",
    )
    ap.add_argument(
        "--private-key-path",
        type=Path,
        help="Tauri updater 私钥文件；默认同 TAURI_PRIVATE_KEY_PATH 或 clients/desktop/tauri-desktop-updater.key",
    )
    ap.add_argument(
        "--private-key-password",
        help="私钥密码；也可设环境变量 TAURI_PRIVATE_KEY_PASSWORD",
    )
    ap.add_argument(
        "--force-sign",
        action="store_true",
        help="与 --sign 合用：即使安装包旁已有 .sig 也重新签名覆盖",
    )
    ap.add_argument("--version", help="版本号（默认读 tauri.conf.json）")
    ap.add_argument(
        "--platform-key",
        default="windows-x86_64",
        help="artifacts 键，如 windows-x86_64（默认 Windows x64）",
    )
    ap.add_argument(
        "--key-prefix",
        default="desktop/releases",
        help="MinIO 对象键前缀",
    )
    ap.add_argument("--notes", default="", help="更新说明（多行可用 --notes-file）")
    ap.add_argument("--notes-file", type=Path, help="从文件读取更新说明")
    ap.add_argument(
        "--pub-date",
        help="ISO8601 发布时间（默认当前 UTC），例 2026-04-07T12:00:00Z",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要执行的操作，实际上传与 POST",
    )
    args = ap.parse_args()

    if args.unsigned and args.sign:
        raise SystemExit("--unsigned 与 --sign 互斥")

    version = normalize_version(args.version or read_release_version())

    installer: Path
    signature: str
    signature_from_discover = False

    if args.unsigned:
        if args.sig:
            print("提示: 已指定 --unsigned，将忽略 --sig", file=sys.stderr)
        installer = resolve_installer_path(args)
        signature = SIGNATURE_UNSIGNED_PLACEHOLDER
    elif not args.installer and not args.sign and not args.sig:
        installer, sig = discover_installer_and_sig(args.bundle_dir.resolve())
        signature = read_signature_text(sig)
        signature_from_discover = True
    elif args.sig and not args.installer:
        raise SystemExit("仅指定 --sig 无效，请同时指定 --installer")
    else:
        installer = resolve_installer_path(args)

    if not args.unsigned and not signature_from_discover:
        if args.sig:
            sig_path = args.sig.resolve()
            if not sig_path.is_file():
                raise SystemExit(f"签名文件不存在: {sig_path}")
            signature = read_signature_text(sig_path)
        elif args.sign:
            key_path = resolve_private_key_path(args.private_key_path)
            pw = args.private_key_password or _env("TAURI_PRIVATE_KEY_PASSWORD")
            found = find_signature_beside(installer)
            if found and not args.force_sign:
                signature = read_signature_text(found)
                print(f"使用已有签名文件: {found}", file=sys.stderr)
            else:
                run_tauri_sign_installer(installer, key_path, pw, args.dry_run)
                if args.dry_run:
                    signature = (
                        "[dry-run] 未写入真实 signature；去掉 --dry-run 后将以 .sig 全文入库"
                    )
                else:
                    found2 = find_signature_beside(installer)
                    if found2 is None:
                        raise SystemExit(
                            "签名后仍未找到 .sig（预期在安装包同目录）。请检查 tauri signer 输出。"
                        )
                    signature = read_signature_text(found2)
        else:
            found = find_signature_beside(installer)
            if found is None:
                raise SystemExit(
                    f"未找到 {installer.name} 旁的 .sig。请指定 --sig，或加 --sign 自动签名，或 --unsigned（仅人工下载）。"
                )
            signature = read_signature_text(found)

    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", installer.name)
    object_key = f"{args.key_prefix.strip('/')}/{version}/{args.platform_key}/{safe_name}"

    notes = args.notes
    if args.notes_file:
        notes = args.notes_file.read_text(encoding="utf-8")

    if args.pub_date:
        pub_date = args.pub_date.strip()
    else:
        pub_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload: dict[str, Any] = {
        "version": version,
        "pub_date": pub_date,
        "artifacts": {
            args.platform_key: {
                "objectKey": object_key,
                "signature": signature,
            }
        },
    }
    if notes:
        payload["notes"] = notes

    upload_file_s3(installer, object_key, args.dry_run)
    post_nexus(payload, args.dry_run)

    if not args.dry_run:
        print("完成。下载站 / Nexus 共用库时将显示该版本。")


if __name__ == "__main__":
    main()
