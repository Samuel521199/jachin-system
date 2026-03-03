# 战役五：隔离区与代码沙箱

**状态**: 已实现（Python 沙箱） | 演进方向：混合动力沙箱  
**定位**: 保卫 Layer 2 绝对安全  
**演进**: 详见 [HYBRID_SANDBOX_ARCHITECTURE.md](./HYBRID_SANDBOX_ARCHITECTURE.md)（WASM + UDS 划时代方案）

---

## 1. 威胁模型

- 恶意插件 `main.py` 中 `os.system("rm -rf /")`
- 静默打包浏览器 Cookie 上传
- 直接 `import` 运行 → 家庭主机沦陷

---

## 2. 两道防线

### 防线一：静态分析 (validator.py)

- **extract_and_validate(jmp_filepath, extract_dir)**：解压 ZIP，校验 manifest.json + main.py
- **scan_python_code(code_str, allowed_permissions)**：AST 遍历，检查 Import/ImportFrom
- **黑名单**：os, subprocess, sys, socket, shutil, ctypes, pickle, marshal, builtins, importlib
- **权限放行**：
  - `system.power` → 允许 os
  - `internet.access` → 允许 requests, aiohttp, urllib, httpx
  - `file.read` / `file.write` → 允许 pathlib, os.path
- **失败**：抛出 `SecurityViolationError`，删除临时目录，阻断加载

### 防线二：受限执行作用域 (sandbox.py)

- **PluginSandbox.load_plugin(plugin_dir, manifest)**：exec() 注入隔离命名空间
- **受限 __builtins__**：禁用 eval, exec, compile, open, __import__, input, breakpoint 等
- **file.read/file.write**：可恢复 open()
- **入口提取**：setup(agent_context) 或 Plugin/Skill/Agent 类

---

## 3. Updater 安全流

```
下载 .jmp → extract_and_validate → [SecurityViolationError] → 清理、WARN、阻断
                ↓ 通过
         sandbox.load_plugin → 复制到 skills_repo → 注册 PluginManager
```

---

## 4. 文件位置

- `core/plugin/validator.py`
- `core/plugin/sandbox.py`
- `core/updater/agent.py`（已更新）

---

## 5. 演进方向：混合动力沙箱

当前 Python AST + __builtins__ 沙箱为过渡方案。**WASM + WASI** 已实现：`core/wasm_runner.py` 支持 Pure Compute（run() -> i32）与 WASI stdin/stdout（Python py2wasm 插件）。重型算力仍规划 UDS/gRPC 独立进程。详见 [HYBRID_SANDBOX_ARCHITECTURE.md](./HYBRID_SANDBOX_ARCHITECTURE.md)。
