#!/usr/bin/env node
/**
 * NSIS/安装包打包前：确保 src-tauri/bin/l3_node-<triple> 存在且为真实 PyInstaller 产物（非 ~1KB 占位 PE）。
 * 若缺失或是占位符，自动运行 scripts/build_l3_sidecar.py。
 *
 * 背景：仅运行 vite build 的 prebuild 若被跳过或侧车构建失败，Tauri 仍可能产出安装包，
 * 但安装后 %LocalAppData%\\...\\bin\\ 无 l3_node，Omni 会一直「等待 L3 或 L2」。
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { spawnSync } from "child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP = path.resolve(__dirname, "..");
const ROOT = path.resolve(DESKTOP, "..", "..");
const TAURI = path.join(DESKTOP, "src-tauri");

/** 占位 stub 约 1KB；真实侧车通常 ≥ 数 MB */
const MIN_BYTES = 256 * 1024;

function sidecarName() {
  if (process.platform !== "win32") {
    console.warn("[ensure-l3-sidecar-for-bundle] 非 Windows：请自行保证 bin/l3_node-<triple> 存在");
    return null;
  }
  return `l3_node-x86_64-pc-windows-msvc.exe`;
}

function runBuild() {
  const py = path.join(ROOT, "scripts", "build_l3_sidecar.py");
  console.error("[ensure-l3-sidecar-for-bundle] 正在执行 python scripts/build_l3_sidecar.py …");
  const r = spawnSync(process.platform === "win32" ? "python" : "python3", [py], {
    cwd: ROOT,
    stdio: "inherit",
    shell: process.platform === "win32",
  });
  if (r.status !== 0) {
    console.error(
      "[ensure-l3-sidecar-for-bundle] build_l3_sidecar.py 失败。请在本机安装 Python 依赖并执行:\n" +
        "  python scripts/build_l3_sidecar.py"
    );
    process.exit(r.status ?? 1);
  }
}

function main() {
  const name = sidecarName();
  if (!name) return;

  const p = path.join(TAURI, "bin", name);
  let need = true;
  if (fs.existsSync(p)) {
    const st = fs.statSync(p);
    if (st.size >= MIN_BYTES) {
      console.log("[ensure-l3-sidecar-for-bundle] OK:", p, `(${st.size} bytes)`);
      need = false;
    } else {
      console.error(
        `[ensure-l3-sidecar-for-bundle] 侧车过小 (${st.size} bytes)，疑似占位符，将重新构建…`
      );
    }
  } else {
    console.error("[ensure-l3-sidecar-for-bundle] 未找到侧车，将运行 PyInstaller 构建…");
  }

  if (need) {
    runBuild();
    if (!fs.existsSync(p)) {
      console.error("[ensure-l3-sidecar-for-bundle] 构建后仍不存在:", p);
      process.exit(1);
    }
    const st2 = fs.statSync(p);
    if (st2.size < MIN_BYTES) {
      console.error(
        `[ensure-l3-sidecar-for-bundle] 构建后文件仍过小 (${st2.size} bytes)，无法用于发布安装包。`
      );
      process.exit(1);
    }
    console.log("[ensure-l3-sidecar-for-bundle] OK:", p, `(${st2.size} bytes)`);
  }
}

main();
