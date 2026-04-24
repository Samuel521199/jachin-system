#!/usr/bin/env node
/**
 * 桌面安装包 / Tauri 产物版本（用户看到的安装程序与关于页版本）。
 * 与仓库根 Git 标签、CHANGELOG、core/cli 的 monorepo 版本无强制一致；全仓备份提交不必改此文件。
 *
 * 单一版本源：编辑上一级的 VERSION（仅一行 x.y.z），然后:
 *   npm run sync-version
 * 或: npm run sync-version -- 0.8.17
 *
 * 会同步到: package.json、src-tauri/tauri.conf.json、src-tauri/Cargo.toml
 *
 * ## 为何一跑 sync-version L3 就重启？
 * `tauri dev` 会监视 `src-tauri/tauri.conf.json` 与 `Cargo.toml`。本脚本默认会改这两处，
 * Cargo/Tauri 触发整进程重建 → 桌面端 `setup()` 里会重新 spawn L3 sidecar，看起来像「L3 重启」。
 * 开发中若不想打断 L3：用 `npm run sync-version:soft -- 0.8.17`（或 `--no-rust`），只改 VERSION + package.json；
 * **打包发版前务必再执行一次完整** `npm run sync-version -- <版本>`，保证安装包与 Cargo 版本一致。
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = path.resolve(__dirname, "..");
const VERSION_FILE = path.join(DESKTOP_ROOT, "VERSION");
const PKG = path.join(DESKTOP_ROOT, "package.json");
const TAURI_CONF = path.join(DESKTOP_ROOT, "src-tauri", "tauri.conf.json");
const CARGO_TOML = path.join(DESKTOP_ROOT, "src-tauri", "Cargo.toml");

const SEMVER_RE = /^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/;

function stripBom(s) {
  return s.charCodeAt(0) === 0xfeff ? s.slice(1) : s;
}

function parseCli() {
  const raw = process.argv.slice(2);
  const noRust =
    raw.includes("--no-rust") ||
    raw.includes("--soft") ||
    process.env.SYNC_VERSION_NO_RUST === "1";
  const versionFromCli = raw
    .map((a) => a.trim())
    .find((a) => a && !a.startsWith("--") && SEMVER_RE.test(a));
  return { noRust, versionFromCli };
}

function readVersionArgOrFile(fromCli) {
  if (fromCli) return fromCli;
  if (!fs.existsSync(VERSION_FILE)) {
    console.error(`缺少版本：请创建 ${VERSION_FILE}（一行 x.y.z）或执行: npm run sync-version -- 0.8.17`);
    process.exit(1);
  }
  const line = fs.readFileSync(VERSION_FILE, "utf8").trim().split(/\r?\n/)[0]?.trim();
  if (!line) {
    console.error(`${VERSION_FILE} 为空`);
    process.exit(1);
  }
  return line;
}

function main() {
  const { noRust, versionFromCli } = parseCli();
  const version = readVersionArgOrFile(versionFromCli);
  if (!SEMVER_RE.test(version)) {
    console.error(`版本格式异常（建议 x.y.z）: ${version}`);
    process.exit(1);
  }

  const pkg = JSON.parse(stripBom(fs.readFileSync(PKG, "utf8")));
  pkg.version = version;
  fs.writeFileSync(PKG, JSON.stringify(pkg, null, 2) + "\n", "utf8");

  fs.writeFileSync(VERSION_FILE, version + "\n", "utf8");

  if (noRust) {
    console.log(
      `[soft] 已同步版本 ${version} → package.json, VERSION（未改 tauri.conf.json / Cargo.toml，避免 tauri dev 重建与 L3 sidecar 重启）`
    );
    console.log(
      "[soft] 打包或发版前请执行完整同步：npm run sync-version -- " + version
    );
    return;
  }

  const tauri = JSON.parse(stripBom(fs.readFileSync(TAURI_CONF, "utf8")));
  tauri.version = version;
  fs.writeFileSync(TAURI_CONF, JSON.stringify(tauri, null, 2) + "\n", "utf8");

  let cargo = fs.readFileSync(CARGO_TOML, "utf8");
  cargo = cargo.replace(
    /^version\s*=\s*"[^"]*"\s*$/m,
    `version = "${version}"`
  );
  if (!/^version\s*=\s*"/m.test(cargo)) {
    console.error("Cargo.toml 中未找到 [package] version 行");
    process.exit(1);
  }
  fs.writeFileSync(CARGO_TOML, cargo, "utf8");

  console.log(`已同步版本 ${version} → package.json, tauri.conf.json, Cargo.toml, VERSION`);
}

main();
