# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('D:\\Projects\\jachi\\jachin-system-main\\docs\\L3_CAPABILITY_CATALOG.md', 'docs'), ('D:\\Projects\\jachi\\jachin-system-main\\docs\\capability_domains', 'docs/capability_domains'), ('D:\\Projects\\jachi\\jachin-system-main\\scripts\\test_kalaroko_default_scenarios_e2e.py', 'scripts'), ('D:\\Projects\\jachi\\jachin-system-main\\scripts\\test_k11_unified_platform_smoke_playwright.py', 'scripts'), ('D:\\Projects\\jachi\\jachin-system-main\\scripts\\test_k11_p2_compat_weaknet_playwright.py', 'scripts'), ('D:\\Projects\\jachi\\jachin-system-main\\scripts\\test_k11_game_open_smoke.py', 'scripts'), ('D:\\Projects\\jachi\\jachin-system-main\\scripts\\test_k11_tongits_autoplay_smoke.py', 'scripts'), ('D:\\Projects\\jachi\\jachin-system-main\\scripts\\k11_tongits_smoke_session.py', 'scripts'), ('D:\\Projects\\jachi\\jachin-system-main\\scripts\\test_k11_game_open_coin_smoke.py', 'scripts'), ('D:\\Projects\\jachi\\jachin-system-main\\scripts\\k11_lark_smoke_report.py', 'scripts')]
binaries = []
hiddenimports = ['l3_node', 'l3_node.standalone_engine', 'l3_node.win_console', 'l3_node.paths', 'l3_node.early_log', 'l3_node.bootstrap', 'l3_node.agent_core', 'l3_node.llm_client', 'l3_node.ws_server', 'l3_node.crypto', 'l3_node.engine.hooks_pipeline', 'l3_node.primitives', 'l3_node.primitives.tools.loader', 'l3_node.primitives.mcp.registry', 'l3_node.primitives.mcp.mcp_tools.human_ask_tool', 'l3_node.primitives.mcp.mcp_tools.lark_bitable_ops', 'l3_node.capability_catalog', 'l3_node.http_server', 'l3_node.k11_subprocess_cli', 'l3_node.packaged_lark_env', 'l3_node.packaged_lark_env_generated', 'l3_node.config_writeout', 'l3_node.im_channels', 'l3_node.im_channels.lark_channel', 'l3_node.im_channels.lark_credentials', 'l3_node.channels.lark.long_connection', 'lark_oapi', 'yaml', 'core.wasm_runner', 'core.single_instance', 'wasmtime', 'websockets', 'litellm', 'litellm.litellm_core_utils', 'tiktoken', 'tiktoken_ext', 'tiktoken_ext.openai_public', 'openai', 'httpx', 'requests', 'urllib3', 'l3_node.jobs.healthchecks_watchdog', 'cryptography', 'playwright', 'playwright.sync_api', 'playwright.async_api', 'playwright_stealth', 'l3_client.local_mcps.jachin_memory_nexus.memory_backend', 'mcp.server.fastmcp', 'dotenv', 'fastembed.text.text_embedding', 'numpy', 'onnxruntime', 'onnxruntime.capi.onnxruntime_pybind11_state', 'PIL', 'PIL.Image', 'pywintypes']
datas += collect_data_files('litellm')
datas += collect_data_files('onnxruntime')
binaries += collect_dynamic_libs('onnxruntime')
hiddenimports += collect_submodules('l3_client.local_mcps.windows_uia_mcp')
hiddenimports += collect_submodules('l3_client.local_mcps.vision_ui_mcp')
hiddenimports += collect_submodules('l3_client.local_mcps.holographic_screen_mcp')
hiddenimports += collect_submodules('l3_client.local_mcps.jachin_memory_nexus')
tmp_ret = collect_all('wasmtime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('litellm')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tiktoken')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tiktoken_ext')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['D:\\Projects\\jachi\\jachin-system-main\\l3_node\\__main__.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'transformers', 'pandas', 'scipy', 'sklearn', 'dask', 'distributed', 'bokeh', 'matplotlib', 'cv2', 'h5py', 'tables', 'PyQt5', 'qtpy', 'numba', 'llvmlite'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='l3_node',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
