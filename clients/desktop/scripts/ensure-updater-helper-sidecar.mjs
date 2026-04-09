#!/usr/bin/env node
/**
 * 在 `tauri build` 打包阶段前，把 `jachin-updater-helper.exe` 复制为 Tauri externalBin 期望的文件名：
 *   src-tauri/bin/jachin-updater-helper-<target-triple>.exe
 * 与 `bin/l3_node-<triple>.exe` 规则一致。
 */
import { spawnSync } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP = path.resolve(__dirname, "..");
const TAURI = path.join(DESKTOP, "src-tauri");
const RELEASE_EXE = path.join(
  TAURI,
  "target",
  "release",
  process.platform === "win32" ? "jachin-updater-helper.exe" : "jachin-updater-helper"
);
const BIN_DIR = path.join(TAURI, "bin");

function targetTriple() {
  if (process.platform === "win32") return "x86_64-pc-windows-msvc";
  if (process.platform === "darwin")
    return process.arch === "arm64" ? "aarch64-apple-darwin" : "x86_64-apple-darwin";
  return process.arch === "arm64" ? "aarch64-unknown-linux-gnu" : "x86_64-unknown-linux-gnu";
}

function main() {
  const triple = targetTriple();
  const ext = process.platform === "win32" ? ".exe" : "";
  const dstName = `jachin-updater-helper-${triple}${ext}`;
  const dst = path.join(BIN_DIR, dstName);

  if (!fs.existsSync(RELEASE_EXE)) {
    const r = spawnSync(
      "cargo",
      ["build", "--release", "--bin", "jachin-updater-helper"],
      { cwd: TAURI, stdio: "inherit", shell: process.platform === "win32" }
    );
    if (r.status !== 0) process.exit(r.status ?? 1);
  }
  if (!fs.existsSync(RELEASE_EXE)) {
    console.error("[ensure-updater-helper-sidecar] 仍不存在:", RELEASE_EXE);
    process.exit(1);
  }

  fs.mkdirSync(BIN_DIR, { recursive: true });
  fs.copyFileSync(RELEASE_EXE, dst);
  console.log("[ensure-updater-helper-sidecar] OK ->", dst);
}

main();
