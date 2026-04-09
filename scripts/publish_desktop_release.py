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

用法 —— 必须在「仓库根目录」jachin-system-main 下执行，或写对相对路径（本脚本不在 clients/desktop 下）:
  cd D:\\path\\to\\jachin-system-main
  python scripts\\publish_desktop_release.py --sign

若在 clients\\desktop 目录，请用:
  python ..\\..\\scripts\\publish_desktop_release.py --sign
  或: npm run publish-desktop-release

其它示例（均在仓库根目录）:
  python scripts/publish_desktop_release.py
    （无 .sig 时若存在 tauri-desktop-updater.key 或 TAURI_PRIVATE_KEY_PATH，将自动签名，等同 --sign）
  python scripts/publish_desktop_release.py --installer path/to/setup.exe --sig path/to/setup.exe.sig
  python scripts/publish_desktop_release.py --installer .../jachin-desktop.exe --unsigned
  python scripts/publish_desktop_release.py --dry-run
  python scripts/publish_desktop_release.py --notes "修复若干问题"

环境变量可写入 cloud/nexus/.env.local；脚本启动时会自动加载（不覆盖已在终端里 export 的值）。

说明:
  - 默认从 clients/desktop/src-tauri/target/release/bundle 下自动发现 .exe/.msi；若有 .sig 则直接读取，否则在能找到私钥时自动 tauri signer sign（等同 --sign）。
  - 发布前会检查：NSIS/MSI 文件名须含发布版本号；对象名由 version 推导。
  - tauri.conf.json 里 bundle.active=false 时只发布 target/release/jachin-desktop.exe，**不会**再扫 bundle/nsis（避免误选历史 setup）。
  - 签名文件：整份 .sig 文件做标准 Base64（单行）后入库，与 tauri-plugin-updater 的 verify_signature 一致；勿传明文多行 .sig。
  - 无 .sig 且无可用私钥时用 --unsigned 写入占位签名（仅发行大厅人工下载；Tauri 热更新签名校验会失败）。
  - crypto_verify 失败：多为「.sig 不是当前这份 exe 签的」（重建 exe 后未重签 / Nexus 复制了旧 signature），其次才是私钥与 tauri.conf pubkey 不成对。默认会拒绝「.sig 早于安装包 mtime」的组合。
  - bundle.active=true（默认）时 Windows 产出 NSIS/MSI 安装包，内含 l3 侧车与热更新助手；用户应下载安装包而非散装主程序 exe。

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
import base64
import json
import os
import re
import shutil
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


def _read_tauri_conf_json() -> dict[str, Any]:
    if not TAURI_CONF.is_file():
        raise FileNotFoundError(f"未找到 {TAURI_CONF}")
    raw = TAURI_CONF.read_text(encoding="utf-8")
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("tauri.conf.json 根节点应为对象")
    return data


def read_version_from_tauri_conf() -> str:
    data = _read_tauri_conf_json()
    v = data.get("version")
    if not v or not isinstance(v, str):
        raise ValueError("tauri.conf.json 缺少 version")
    return v.strip()


def tauri_bundle_active() -> bool:
    """
    与 tauri.conf.json 的 bundle.active 一致。
    为 false 时 `tauri build` 不产出 NSIS/MSI，bundle/ 下可能残留历史 setup，发布脚本不得优先选用。
    """
    try:
        data = _read_tauri_conf_json()
    except (OSError, ValueError, json.JSONDecodeError):
        return True
    b = data.get("bundle")
    if not isinstance(b, dict):
        return True
    return bool(b.get("active", True))


def read_release_version() -> str:
    """优先 clients/desktop/VERSION（与 npm run sync-version 一致），否则读 tauri.conf.json。"""
    if VERSION_FILE.is_file():
        first = VERSION_FILE.read_text(encoding="utf-8").strip().splitlines()
        if first and first[0].strip():
            return first[0].strip()
    return read_version_from_tauri_conf()


def reject_if_detached_sig_older_than_installer(
    installer: Path,
    sig_path: Path,
    *,
    allow_stale: bool,
) -> None:
    """
    安装包重建后若仍沿用旧 .sig，minisign 会在 crypto_verify 失败（日志里 pubkey_OK、Signature_decode_OK 后报错）。
    与公私钥是否成对无关；常见于未加 --force-sign、或 Nexus 里复制了旧版本的 signature 字段。
    """
    try:
        im = installer.stat().st_mtime
        sm = sig_path.stat().st_mtime
    except OSError:
        return
    if im <= sm + 1.0:
        return
    msg = (
        f"拒绝使用陈旧签名: {sig_path.name} 早于安装包 {installer.name} 生成（mtime）。\n"
        "当前 .sig 不是针对这份二进制签的，热更新会报 The signature verification failed。\n"
        "请执行: npx tauri signer sign -f <私钥> <安装包>，或发布时加 --sign --force-sign。\n"
        "若你确信无误，可加 --allow-stale-signature。"
    )
    if allow_stale:
        print(f"[WARN] {msg}", file=sys.stderr)
    else:
        raise SystemExit(msg)


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


def pick_published_artifact(bundle: Path) -> Path | None:
    """
    选择待上传的安装介质：bundle.active=true 时优先 NSIS/MSI；否则只用 target/release 主程序 exe，
    避免误选 bundle/nsis 里上次打安装包留下的旧 *_0.8.74_*-setup.exe。
    """
    if not tauri_bundle_active():
        print(
            "[INFO] tauri.conf.json 中 bundle.active=false：使用 target/release/jachin-desktop.exe（或同目录最大 .exe），"
            "不扫描 bundle/nsis 以免误发历史 NSIS。若要发布 setup.exe，请先将 bundle.active 设为 true 并重新 tauri build。",
            file=sys.stderr,
        )
        rel = pick_release_dir_exe()
        if rel is not None:
            return rel
        return pick_windows_installer(bundle)

    inst = pick_windows_installer(bundle)
    if inst is not None:
        return inst
    return pick_release_dir_exe()


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


def discover_installer(bundle: Path) -> Path:
    """仅解析安装包路径；不要求旁路已有 .sig。"""
    inst = pick_published_artifact(bundle)
    if inst is None:
        raise FileNotFoundError(
            f"在 {bundle} 与 target/release 下未发现 .exe/.msi。"
            "请先 tauri build 或使用 --installer 指定路径。"
        )
    return inst


def ensure_jachin_updater_helper_beside_main(main_exe: Path) -> None:
    """
    「立即更新」要求 jachin-updater-helper 与**已安装的主程序**同目录。
    - 散装 target/release/jachin-desktop.exe：助手应在同目录（发布前可自动编译并期望已拷贝）。
    - NSIS/MSI 安装包：助手由 Tauri externalBin 打入安装目录，不要求与 setup.exe 同目录。
    """
    helper_name = "jachin-updater-helper.exe" if sys.platform == "win32" else "jachin-updater-helper"
    helper = main_exe.parent / helper_name
    if helper.is_file():
        return

    release_helper = DESKTOP_ROOT / "src-tauri" / "target" / "release" / helper_name
    is_windows_setup = sys.platform == "win32" and (
        main_exe.name.endswith("-setup.exe") or "nsis" in main_exe.parts
    )
    if is_windows_setup:
        if release_helper.is_file():
            print(
                "[INFO] 热更新助手已随 NSIS externalBin 打入安装目录；无需与 setup.exe 并列。",
                file=sys.stderr,
            )
        else:
            print(
                "[WARN] 未找到 target/release 下的热更新助手；请确认 tauri build 已执行且 "
                "beforeBundleCommand（ensure-updater-helper-sidecar）成功。",
                file=sys.stderr,
            )
        return

    print(
        f"[WARN] 未找到热更新助手（预期与 {main_exe.name} 同目录: {main_exe.parent}），"
        "正在编译 release: jachin-updater-helper …",
        file=sys.stderr,
    )
    st = subprocess.run(
        ["cargo", "build", "--release", "--bin", "jachin-updater-helper"],
        cwd=str(DESKTOP_ROOT / "src-tauri"),
    )
    if st.returncode != 0:
        raise SystemExit(
            "编译 jachin-updater-helper 失败。请在 clients/desktop/src-tauri 执行:\n"
            "  cargo build --release --bin jachin-updater-helper"
        )
    if not helper.is_file():
        raise SystemExit(
            f"编译后仍未找到 {helper}。\n"
            "若仅将主程序拷到 Downloads 等目录测试，请同时拷贝同目录下的 "
            f"{helper_name}（与 jachin-desktop.exe 来自同一次构建）。"
        )
    print(f"[OK] 热更新助手: {helper}", file=sys.stderr)


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
    """
    Tauri updater / jachin-updater-helper：JSON 里 signature 须为「整份 .sig 字节的标准 Base64」单行。
    对文件原始字节编码，并去掉一切空白（防止工具或拷贝误插入换行）。
    """
    data = sig_path.read_bytes()
    if not data:
        raise ValueError(f"签名文件为空: {sig_path}")
    b64 = base64.standard_b64encode(data).decode("ascii")
    return "".join(b64.split())


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


def installer_path_expects_version_in_filename(installer_path: Path) -> bool:
    """NSIS/MSI 安装包文件名通常带版本；散装 jachin-desktop.exe 不带。"""
    n = installer_path.name.lower()
    if n.endswith(".msi"):
        return True
    return "setup" in n or "-setup" in n or "_setup" in n


def version_appears_in_installer_filename(version: str, installer_path: Path) -> bool:
    """
    本地安装包文件名是否包含将要发布的 semver（不含 v 前缀）。
    避免 bump 到 0.8.75 却上传 bundle 里仍为 0.8.74 文件名的旧构建，导致「目录是 0.8.75、文件名像 0.8.74」。
    """
    v = version.strip().lstrip("vV")
    if not v:
        return False
    name = installer_path.name.lower()
    if v.lower() in name:
        return True
    alt = v.replace(".", "_").lower()
    return alt in name


def remote_artifact_basename(version: str, platform_key: str, installer: Path) -> str:
    """
    MinIO 对象名的 basename：由发布 version 推导，与路径段 …/{version}/{platform}/ 一致，
    不沿用本地可能过期的「0.8.74」文件名。
    """
    v = version.strip().lstrip("vV")
    ext = installer.suffix.lower()
    if ext == ".msi":
        return f"jachin-desktop-{v}-{platform_key}.msi"
    lower = installer.name.lower()
    is_setup = "setup" in lower or "-setup" in lower or "_setup" in lower
    if ext == ".exe" and is_setup:
        return f"jachin-desktop-{v}-{platform_key}-setup.exe"
    if ext == ".exe":
        return f"jachin-desktop-{v}-{platform_key}.exe"
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", installer.name)


def resolve_installer_path(args: argparse.Namespace) -> Path:
    """与 --unsigned 相同的安装包发现逻辑（显式 --installer 或 bundle/release 自动发现）。"""
    if args.installer:
        installer = args.installer.resolve()
        if not installer.is_file():
            raise SystemExit(f"安装包不存在: {installer}")
        return installer
    bd = args.bundle_dir.resolve()
    inst = pick_published_artifact(bd)
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


def try_locate_private_key_path(cli_path: Path | None) -> Path | None:
    """
    查找可用于签名的私钥路径；找不到返回 None（不抛）。
    若显式传入 --private-key-path 但文件不存在，仍抛 SystemExit（与 resolve_private_key_path 一致）。
    """
    if cli_path is not None:
        p = cli_path.expanduser().resolve()
        if not p.is_file():
            raise SystemExit(f"私钥文件不存在: {p}")
        return p
    env_p = _env("TAURI_PRIVATE_KEY_PATH")
    if env_p:
        p = Path(env_p).expanduser().resolve()
        if p.is_file():
            return p
    default = DEFAULT_TAURI_UPDATER_KEY.resolve()
    if default.is_file():
        return default
    return None


def _resolve_npx_executable() -> str:
    """
    Windows 上 subprocess 直接启动「npx」常失败（WinError 2），需解析到 npx.cmd / 完整路径。
    """
    if sys.platform == "win32":
        for name in ("npx.cmd", "npx.exe", "npx"):
            p = shutil.which(name)
            if p:
                return p
    p = shutil.which("npx")
    if p:
        return p
    raise SystemExit(
        "未在 PATH 中找到 npx（已尝试 npx.cmd / npx.exe）。请安装 Node.js 并重新打开终端，\n"
        "或在 clients/desktop 手动执行：\n"
        "  npx tauri signer sign -f <私钥路径> <安装包.exe>\n"
        "生成 .sig 后再： python scripts\\publish_desktop_release.py（不要 --sign）"
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
    npx = _resolve_npx_executable()
    cmd: list[str] = [
        npx,
        "tauri",
        "signer",
        "sign",
        "-f",
        str(private_key_path.resolve()),
        str(installer.resolve()),
    ]
    if password:
        cmd.extend(["-p", password])
    print("运行:", npx, "tauri signer sign -f ... sign", installer.name, file=sys.stderr)
    r = subprocess.run(cmd, cwd=str(DESKTOP_ROOT), shell=False)
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
    ap.add_argument(
        "--allow-artifact-filename-version-mismatch",
        action="store_true",
        help="允许安装包文件名不含发布版本号（易把旧构建登记为新版本，仅应急）",
    )
    ap.add_argument(
        "--allow-stale-signature",
        action="store_true",
        help="允许 .sig 早于安装包 mtime（默认禁止，避免热更新验签在 crypto_verify 阶段失败）",
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
        installer = discover_installer(args.bundle_dir.resolve())
        sig = find_signature_beside(installer)
        if sig is not None:
            reject_if_detached_sig_older_than_installer(
                installer, sig, allow_stale=args.allow_stale_signature
            )
            signature = read_signature_text(sig)
            signature_from_discover = True
        else:
            key_path = try_locate_private_key_path(args.private_key_path)
            if key_path is None:
                raise SystemExit(
                    f"未找到与 {installer.name} 对应的 .sig（尝试过 {installer.name}.sig 及同目录唯一 *.sig）。\n"
                    "可选：\n"
                    "  • 加 --sign 自动签名（需私钥在默认路径或 TAURI_PRIVATE_KEY_PATH）；\n"
                    "  • 或先执行: npx tauri signer sign -f <私钥> <安装包>，再重新运行本脚本；\n"
                    "  • 或 --installer 与 --sig 同时指定；\n"
                    "  • 或 --unsigned（仅人工下载，热更新签名校验不可用）。\n"
                    "私钥说明见脚本顶部「正式签名」一节。"
                )
            pw = args.private_key_password or _env("TAURI_PRIVATE_KEY_PASSWORD")
            print(
                "[INFO] 未在安装包旁发现 .sig；已找到 updater 私钥，将自动执行 tauri signer sign（与 --sign 相同）。",
                file=sys.stderr,
            )
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
            # 避免后续「无 .sig」分支在 dry-run 或未标记 discover 时重复校验或覆盖 signature
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
            reject_if_detached_sig_older_than_installer(
                installer, sig_path, allow_stale=args.allow_stale_signature
            )
            signature = read_signature_text(sig_path)
        elif args.sign:
            key_path = resolve_private_key_path(args.private_key_path)
            pw = args.private_key_password or _env("TAURI_PRIVATE_KEY_PASSWORD")
            found = find_signature_beside(installer)
            if found and not args.force_sign:
                reject_if_detached_sig_older_than_installer(
                    installer, found, allow_stale=args.allow_stale_signature
                )
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
            reject_if_detached_sig_older_than_installer(
                installer, found, allow_stale=args.allow_stale_signature
            )
            signature = read_signature_text(found)

    ensure_jachin_updater_helper_beside_main(installer)

    if (
        not args.allow_artifact_filename_version_mismatch
        and installer_path_expects_version_in_filename(installer)
        and not version_appears_in_installer_filename(version, installer)
    ):
        raise SystemExit(
            f"安装包「{installer.name}」文件名中未找到将要发布的版本号「{version}」。\n"
            "常见原因：已把 VERSION / tauri.conf 改成新版本，但未重新执行 tauri build，"
            "bundle 里仍是上一版的 setup。\n\n"
            "请先完成对应版本的构建再发布；若坚持上传当前文件，请加 "
            "--allow-artifact-filename-version-mismatch（不推荐，易与真实二进制版本不一致）。"
        )

    safe_name = remote_artifact_basename(version, args.platform_key, installer)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", safe_name)
    object_key = f"{args.key_prefix.strip('/')}/{version}/{args.platform_key}/{safe_name}"
    if installer.name != safe_name:
        print(
            f"[INFO] 对象存储内文件名: {safe_name}（与发布 version={version} 对齐）；"
            f"本地文件: {installer.name}",
            file=sys.stderr,
        )

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
        print(
            "提示：热更新「立即更新」需 jachin-updater-helper(.exe) 与主程序同目录；"
            "若把 exe 拷到 Downloads 等目录手工测，请两枚一起拷贝（同一次 tauri build 产物）。",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
