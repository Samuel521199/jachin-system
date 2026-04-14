Jachin MCP embedded runtime layout (no system Python/Node required on user PC)
================================================================================

1) Directory layout (pick one root; first match wins in core/mcp_embedded_runtime.py)

   A) Portable app: %JACHIN_APP_ROOT%\runtime\
   B) User home:    %USERPROFILE%\.jachin\runtime\   (override home with JACHIN_HOME)
   C) PyInstaller / Sidecar: same directory as l3_node-*.exe\runtime\  (see docs/L3_EMBEDDED_RUNTIME.md)

   Under runtime/:

     python\python.exe           Windows (CPython embeddable zip extracted here)
     python\bin\python3        Linux / macOS venv or official tarball layout

     node\node.exe               Windows portable Node (official node-v*-win-x64.zip contents)
     node\npx.cmd                Windows — REQUIRED for "npx -y <package>" MCP 与裸 command=npx
     node\npm.cmd                Windows — 可选，部分脚本会调用 npm
     node\bin\node               Unix portable Node official tarball
     node\bin\npx                Unix — 若发行版提供独立 npx 可执行文件

   将官方 Node **Windows x64** zip 解压时，应使 **node.exe、npx.cmd、npm.cmd 位于同一目录**，
   将该目录作为上述 runtime\node\ 的内容（或把 zip 内层文件夹改名为 node 再放入 runtime）。

   Optional: copy manifest.example.json to runtime/manifest.json and set versions
   for support / updates (resolver does not read it today).

2) Environment overrides (highest priority)

   JACHIN_MCP_PYTHON   full path to python.exe or python3
   JACHIN_MCP_NODE     full path to node executable
   JACHIN_MCP_NPX      full path to npx (e.g. ...\npx.cmd on Windows)
   JACHIN_MCP_NPM      full path to npm (optional)

3) plugin.json / mcp_servers.json placeholders

   __JACHIN_MCP_PYTHON__  -> embedded or PATH python
   __JACHIN_MCP_NODE__    -> embedded or PATH node
   __JACHIN_MCP_NPX__     -> embedded npx.cmd or PATH npx

   Bare "python" / "python3" / "node" / "npx" / "npm" in command is rewritten to embedded path
   when that executable exists under runtime/ (裸命令才替换，显式绝对路径不会被覆盖).

4) PyPI packages for official MCP (into embedded Python)

   pip install -r tools/mcp-official/requirements-official-mcp.txt
   (Run using the SAME interpreter as JACHIN_MCP_PYTHON / runtime/python.)

5) Windows CPython embeddable

   Download embeddable package from python.org, extract to runtime/python/,
   enable import site (see python.org embeddable README), then pip install
   mcp-server-fetch into that prefix or use python -m ensurepip if enabled.

6) Preflight errors

   If stdio MCP fails preflight, logs show [MCP Runtime] with hints to install
   runtime/ or set JACHIN_MCP_PYTHON / JACHIN_MCP_NODE / JACHIN_MCP_NPX.

7) 详见 docs/L3_EMBEDDED_RUNTIME.md（L3 打包与桌面安装器如何附带 runtime）。
