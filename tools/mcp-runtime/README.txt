Jachin MCP embedded runtime layout (no system Python/Node required on user PC)
================================================================================

1) Directory layout (pick one root; first match wins in core/mcp_embedded_runtime.py)

   A) Portable app: %JACHIN_APP_ROOT%\runtime\
   B) User home:    %USERPROFILE%\.jachin\runtime\   (override home with JACHIN_HOME)

   Under runtime/:

     python\python.exe           Windows (CPython embeddable zip extracted here)
     python\bin\python3          Linux / macOS venv or official tarball layout
     node\node.exe               Windows portable Node
     node\bin\node               Unix portable Node

   Optional: copy manifest.example.json to runtime/manifest.json and set versions
   for support / updates (resolver does not read it today).

2) Environment overrides (highest priority)

   JACHIN_MCP_PYTHON   full path to python.exe or python3
   JACHIN_MCP_NODE     full path to node executable

3) plugin.json / mcp_servers.json placeholders

   __JACHIN_MCP_PYTHON__  -> embedded or PATH python
   __JACHIN_MCP_NODE__    -> embedded or PATH node

   Bare "python" / "python3" / "node" in command is rewritten to embedded path
   when that executable exists under runtime/.

4) PyPI packages for official MCP (into embedded Python)

   pip install -r tools/mcp-official/requirements-official-mcp.txt
   (Run using the SAME interpreter as JACHIN_MCP_PYTHON / runtime/python.)

5) Windows CPython embeddable

   Download embeddable package from python.org, extract to runtime/python/,
   enable import site (see python.org embeddable README), then pip install
   mcp-server-fetch into that prefix or use python -m ensurepip if enabled.

6) Preflight errors

   If stdio MCP fails preflight, logs show [MCP Runtime] with hints to install
   runtime/ or set JACHIN_MCP_PYTHON / JACHIN_MCP_NODE.
