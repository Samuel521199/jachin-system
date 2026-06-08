#!/usr/bin/env python3
"""
L3 节点 PyInstaller 打包脚本 — 产出 Tauri Sidecar 二进制

用法（在项目根目录执行）:
  python scripts/build_l3_sidecar.py [--force]
  --force  强制重新打包，忽略「二进制比源码新」的跳过逻辑

产出:
  clients/desktop/src-tauri/bin/l3_node-{target_triple}[.exe]
  （与 tauri.conf.json 的 bundle.externalBin: bin/l3_node 对应）

  便携包基线：成功后将仓库根 ``.env.example`` 复制为 ``dist_jachin_desktop/.env.example``，
  与桌面安装/巡检中枢（Kalaroko CDP、Lark、LLM、Healthchecks 业务绑定 ping 等）说明保持同源，避免 dist 与仓库脱节。

  打侧车前会运行 ``emit_packaged_lark_env``：自仓库根 ``.env`` 提取 ``LARK_*`` / ``K11_SMOKE_LARK_*`` 等
  写入 ``l3_node/packaged_lark_env_generated.py`` 并随 exe 内嵌（目标机可不再配 .env；仍可用安装目录 .env 覆盖）

  Healthchecks（``l3_node/jobs/healthchecks_watchdog.py``）经 ``--hidden-import`` 与 ``requests``/``urllib3``
  一并打入单文件 exe，避免 frozen 下缺模块导致巡检成功后无法 ping。

  部署时若需 npx 类 MCP 且无系统 Node：将官方 Node zip 解压到
  「exe 同目录/runtime/node/」（含 node.exe、npx.cmd），见 docs/L3_EMBEDDED_RUNTIME.md。

依赖:
  pip install pyinstaller
  pip install -r core/requirements.txt  # l3_node 依赖
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = ROOT / "clients" / "desktop" / "src-tauri" / "bin"
SIDECAR_NAME = "l3_node"


def _sync_dist_jachin_desktop_env_example() -> None:
    """
    将仓库根 ``.env.example`` 同步到 ``dist_jachin_desktop/.env.example``。

    巡检中枢（/api/v1/monitor/*、Kalaroko MCP、定时巡检/晨报）依赖的键说明以根文件为 SSOT；
    每次打 sidecar 时刷新便携目录，避免旧 dist 缺键。
    """
    src = ROOT / ".env.example"
    dst = ROOT / "dist_jachin_desktop" / ".env.example"
    if not src.is_file():
        print(f"      [WARN] 未找到 {src}，跳过 dist_jachin_desktop/.env.example 同步")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
        print(f"      已同步便携基线 .env.example → {dst}")
    except OSError as e:
        print(f"      [WARN] 复制 .env.example 到 dist_jachin_desktop 失败: {e}")


def get_target_triple() -> str:
    """通过 rustc 获取当前平台 target triple。"""
    try:
        out = subprocess.run(
            ["rustc", "--print", "host-tuple"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(ROOT),
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # 无 rustc 时按 platform 推断
        machine = platform.machine().lower()
        if machine in ("amd64", "x64"):
            machine = "x86_64"
        system = platform.system().lower()
        if system == "windows":
            return f"{machine}-pc-windows-msvc"
        if system == "darwin":
            return f"{machine}-apple-darwin"
        return f"{machine}-unknown-linux-gnu"


def _patch_pyinstaller_create_base_library_zip() -> None:
    """
    PyInstaller 在 assemble 阶段写入 CONF['workpath']/base_library.zip 时，
    在部分环境（含 Windows + 自定义 --workpath）下父目录可能尚未创建，触发 FileNotFoundError。
    build_main 在模块导入时 from utils import create_base_library_zip，仅 patch utils 无效，须同时替换 build_main 全局名。
    """
    import PyInstaller.building.build_main as bm
    import PyInstaller.building.utils as bu

    _orig = bu.create_base_library_zip

    def _wrapped(filename, modules_toc, code_cache=None):
        parent = os.path.dirname(os.path.abspath(filename))
        if parent:
            os.makedirs(parent, exist_ok=True)
        return _orig(filename, modules_toc, code_cache)

    bu.create_base_library_zip = _wrapped
    bm.create_base_library_zip = _wrapped


def main() -> int:
    os.chdir(ROOT)

    # 自 .env 生成内嵌飞书键（Lark/K11），无 .env 则空表，PyInstaller 仍打入内嵌模块
    _lp = ROOT / "scripts" / "emit_packaged_lark_env.py"
    if _lp.is_file():
        import importlib.util

        _spec = importlib.util.spec_from_file_location("emit_packaged_lark_env", str(_lp))
        if _spec and _spec.loader:
            _m = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_m)
            _emit = getattr(_m, "emit", None)
            if callable(_emit):
                _ec = int(_emit(ROOT))
                if _ec != 0:
                    return _ec
    else:
        print("      [WARN] 未找到 scripts/emit_packaged_lark_env.py，跳过内嵌 Lark 键")

    # 检查 PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("请先安装 PyInstaller: pip install pyinstaller")
        return 1

    # 检查 l3_node 存在
    l3_main = ROOT / "l3_node" / "__main__.py"
    if not l3_main.exists():
        print("错误: l3_node/__main__.py 未找到")
        return 1

    # 提前计算目标路径，若已存在且比源码新则跳过
    ext = ".exe" if sys.platform == "win32" else ""
    target = get_target_triple()
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = BIN_DIR / f"{SIDECAR_NAME}-{target}{ext}"
    force = "--force" in sys.argv
    if dst.exists() and not force:
        py_files = [p for p in (ROOT / "l3_node").rglob("*.py") if p.is_file()]
        src_mtime = max((p.stat().st_mtime for p in py_files), default=0)
        if py_files and dst.stat().st_mtime >= src_mtime:
            print(f"[跳过] 二进制已存在且比源码新: {dst}")
            return 0

    # 排除 Anaconda 中 L3 不需要的重型包（torch/transformers 等会触发 DLL 错误、pandas 等会拖慢构建）；
    # 不排除 l3_node.primitives.mcp.mcp_tools（K11 Lark 同步、registry 内建原子 MCP 依赖）。
    # 远端 MCP/Skill 仍可通过订阅下载到 l3_mcp_cache/l3_skill_cache 使用
    exclude_modules = [
        "torch", "torchvision", "transformers",  # WinError 1114 DLL 初始化失败
        "pandas", "scipy", "sklearn", "dask", "distributed",  # 非 L3 依赖
        "bokeh", "matplotlib", "PIL", "cv2", "h5py", "tables",  # 非 L3 依赖
        "PyQt5", "qtpy", "onnxruntime", "numba", "llvmlite",  # 非 L3 依赖
    ]
    # 不用 --clean：脚本上方已 rmtree dist_l3/build_l3；PyInstaller --clean 会清空 workpath 子目录，易与 base_library.zip 路径竞态
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",  # Windows: 不弹黑框; Unix: 无影响
        "-n", SIDECAR_NAME,
        "--distpath", str(ROOT / "dist_l3"),
        "--workpath", str(ROOT / "build_l3"),
        "--specpath", str(ROOT),
    ]
    for mod in exclude_modules:
        cmd.extend(["--exclude-module", mod])
    # 勿排除 l3_node.primitives.mcp.mcp_tools：frozen 下 ``scripts/k11_lark_smoke_report.py``（--add-data 内嵌）
    # 会 import ``primitives.mcp.mcp_tools.bi.lark_bitable_client`` 等；排除会导致缺子包。
    # 子树以 collect-submodules 打入，供 registry 懒加载的 L3 本地 MCP 原子工具同路径可用。
    # 能力总目录：供 capability_catalog 在 frozen 下从 sys._MEIPASS/docs 读取（与 l3_node/capability_catalog._docs_dirs 一致）
    _docs_sep = ";" if sys.platform == "win32" else ":"
    _cat_md = ROOT / "docs" / "L3_CAPABILITY_CATALOG.md"
    _domains = ROOT / "docs" / "capability_domains"
    if _cat_md.is_file():
        cmd.extend(["--add-data", f"{_cat_md}{_docs_sep}docs"])
    if _domains.is_dir():
        cmd.extend(["--add-data", f"{_domains}{_docs_sep}docs/capability_domains"])
    # Kalaroko 巡检中枢 / SSE：运行时按路径 load 该脚本（frozen 下须在 _MEIPASS/scripts/）
    _kalaroko_e2e = ROOT / "scripts" / "test_kalaroko_default_scenarios_e2e.py"
    if _kalaroko_e2e.is_file():
        cmd.extend(["--add-data", f"{_kalaroko_e2e}{_docs_sep}scripts"])
    # K11 统合 / P2 兼容：http_server 子进程入口依赖 _MEIPASS/scripts 内嵌副本（与 Kalaroko 巡检一致）
    _k11_uni = ROOT / "scripts" / "test_k11_unified_platform_smoke_playwright.py"
    if _k11_uni.is_file():
        cmd.extend(["--add-data", f"{_k11_uni}{_docs_sep}scripts"])
    _k11_p2 = ROOT / "scripts" / "test_k11_p2_compat_weaknet_playwright.py"
    if _k11_p2.is_file():
        cmd.extend(["--add-data", f"{_k11_p2}{_docs_sep}scripts"])
    # K11 游戏模块开门冒烟：与 paths.k11_game_open_smoke_script_path / http_server 子进程一致
    _k11_game_open = ROOT / "scripts" / "test_k11_game_open_smoke.py"
    if _k11_game_open.is_file():
        cmd.extend(["--add-data", f"{_k11_game_open}{_docs_sep}scripts"])
    _k11_lark = ROOT / "scripts" / "k11_lark_smoke_report.py"
    if _k11_lark.is_file():
        cmd.extend(["--add-data", f"{_k11_lark}{_docs_sep}scripts"])
    # PMO Copilot：桌面端 --run-pmo-copilot 入口（frozen 下须在 _MEIPASS/scripts/）
    _pmo_cli = ROOT / "scripts" / "run_pmo_copilot_skill.py"
    if _pmo_cli.is_file():
        cmd.extend(["--add-data", f"{_pmo_cli}{_docs_sep}scripts"])
    cmd += [
        "--hidden-import", "l3_node",
        "--hidden-import", "l3_node.pmo_copilot_cli",
        "--hidden-import", "l3_node.pmo_skill_paths",
        "--hidden-import", "l3_node.pmo_copilot_env",
        "--hidden-import", "l3_node.standalone_engine",
        "--hidden-import", "l3_node.pmo_mcp_delegate",
        "--hidden-import", "l3_node.win_console",
        "--hidden-import", "l3_node.paths",
        "--hidden-import", "l3_node.early_log",
        "--hidden-import", "l3_node.bootstrap",
        "--hidden-import", "l3_node.agent_core",
        "--hidden-import", "l3_node.llm_client",
        "--hidden-import", "l3_node.ws_server",
        "--hidden-import", "l3_node.crypto",
        "--hidden-import", "l3_node.engine.hooks_pipeline",
        "--hidden-import", "l3_node.primitives",
        "--hidden-import", "l3_node.primitives.tools.loader",
        "--hidden-import", "l3_node.primitives.mcp.registry",
        "--collect-submodules", "l3_node.primitives.mcp.mcp_tools",
        "--hidden-import", "l3_node.hr_loader",
        "--hidden-import", "l3_node.capability_catalog",
        "--hidden-import", "l3_node.http_server",
        "--hidden-import", "l3_node.k11_subprocess_cli",
        "--hidden-import", "l3_node.packaged_lark_env",
        "--hidden-import", "l3_node.packaged_lark_env_generated",
        "--hidden-import", "l3_node.config_writeout",
        "--hidden-import", "l3_node.im_channels",
        "--hidden-import", "l3_node.im_channels.lark_channel",
        "--hidden-import", "l3_node.im_channels.lark_credentials",
        "--hidden-import", "l3_node.im_channels.pmo_bitable_channel",
        "--hidden-import", "l3_node.channels.lark.long_connection",
        "--hidden-import", "lark_oapi",
        "--hidden-import", "yaml",
        "--hidden-import", "core.wasm_runner",
        "--hidden-import", "core.single_instance",
        "--hidden-import", "wasmtime",
        "--collect-all", "wasmtime",
        "--hidden-import", "websockets",
        "--hidden-import", "litellm",
        "--hidden-import", "litellm.litellm_core_utils",
        "--collect-all", "litellm",
        "--collect-data", "litellm",  # 显式收集 model_prices_and_context_window_backup.json 等，避免 frozen 下 FileNotFoundError
        "--hidden-import", "tiktoken",
        "--hidden-import", "tiktoken_ext",
        "--hidden-import", "tiktoken_ext.openai_public",
        "--collect-all", "tiktoken",
        "--collect-all", "tiktoken_ext",
        "--hidden-import", "openai",
        "--hidden-import", "httpx",
        "--hidden-import", "requests",
        "--hidden-import", "urllib3",
        # Healthchecks（kalaroko_inspection_notify 内动态 import，须显式打入 frozen）
        "--hidden-import", "l3_node.jobs.healthchecks_watchdog",
        "--hidden-import", "cryptography",
        "--hidden-import", "playwright",
        "--hidden-import", "playwright.sync_api",
        "--hidden-import", "playwright.async_api",
        "--hidden-import", "playwright_stealth",
        # Kalaroko E2E / 巡检中枢：脚本内动态 import l3_client.*；须显式打入 frozen
        "--collect-submodules", "l3_client",
        "--hidden-import", "l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor",
        "--hidden-import", "l3_client.local_mcps.jachin_memory_nexus.memory_backend",
        "--hidden-import", "l3_node.kalaroko_e2e_control",
        "--hidden-import", "l3_node.channels.lark.kalaroko_inspection_notify",
        "--hidden-import", "mcp.server.fastmcp",
        "--hidden-import", "dotenv",
        # Memory Nexus commit_drawer（E2E 异常入库时；lazy import 须显式收集）
        "--hidden-import", "fastembed.text.text_embedding",
        "--hidden-import", "numpy",
        str(l3_main),
    ]
    if sys.platform == "win32":
        # mcp stdio 路径会触达 pywintypes（见 core/requirements.txt 注释）
        cmd.extend(["--hidden-import", "pywintypes"])

    # 彻底清理并预创建构建目录，避免 FileNotFoundError: base_library.zip（父目录不存在）
    for d in ["dist_l3", "build_l3"]:
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)
    (ROOT / "build_l3" / SIDECAR_NAME).mkdir(parents=True, exist_ok=True)
    (ROOT / "dist_l3").mkdir(parents=True, exist_ok=True)

    print("[1/3] 运行 PyInstaller...")
    _patch_pyinstaller_create_base_library_zip()
    from PyInstaller.__main__ import run as pyi_run

    pyi_args = cmd[3:]
    try:
        pyi_run(pyi_args=pyi_args)
    except SystemExit as e:
        code = e.code
        if code not in (None, 0):
            return code if isinstance(code, int) else 1

    # 复制到 bin 目录（target 已在上面计算）
    src = ROOT / "dist_l3" / f"{SIDECAR_NAME}{ext}"
    if not src.exists():
        print(f"错误: 打包产物不存在 {src}")
        return 1

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = BIN_DIR / f"{SIDECAR_NAME}-{target}{ext}"
    shutil.copy2(src, dst)
    print(f"[2/3] 已复制到 {dst}")

    # 同时复制到 dist_jachin_desktop/bin（便携包最终运行目录）
    dist_bin = ROOT / "dist_jachin_desktop" / "bin"
    dist_bin.mkdir(parents=True, exist_ok=True)
    dst_dist = dist_bin / f"{SIDECAR_NAME}-{target}{ext}"
    dst_new = dist_bin / f"{SIDECAR_NAME}-{target}.new{ext}"
    try:
        shutil.copy2(src, dst_new)
        try:
            dst_dist.unlink(missing_ok=True)
            shutil.move(str(dst_new), str(dst_dist))
            print(f"      已复制到 {dst_dist}")
        except (PermissionError, OSError) as e:
            print(f"      [WARN] 无法覆盖 {dst_dist}（可能 L3 正在运行）: {e}")
            print(f"      新 exe 已保存为 {dst_new}")
            print("      请关闭 L3 后执行: ren 或 move 将 .new 替换为正式文件")
    except Exception as e:
        print(f"      [ERR] 复制到 dist_jachin_desktop 失败: {e}")

    _sync_dist_jachin_desktop_env_example()

    # 清理临时目录
    for d in ["dist_l3", "build_l3"]:
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)
    spec = ROOT / f"{SIDECAR_NAME}.spec"
    if spec.exists():
        spec.unlink()
    print("[3/3] 临时文件已清理")

    print(f"\n完成。Sidecar 二进制: {dst}")
    print("下一步: cd clients/desktop && npm run tauri dev")
    print(
        "\n[MCP/npx] 目标机无系统 Node 时：将官方 Node 便携包解压到 exe 旁 runtime\\node\\（须含 npx.cmd），"
        "或使用 scripts/stage-l3-mcp-node-runtime.ps1 复制到 %USERPROFILE%\\.jachin\\runtime\\node\\ — "
        "详见 docs/L3_EMBEDDED_RUNTIME.md"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
