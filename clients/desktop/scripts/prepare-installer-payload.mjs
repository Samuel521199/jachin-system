#!/usr/bin/env node
/**
 * tauri build 的 beforeBuildCommand（末尾）与 beforeBundleCommand：确保 dist_jachin_desktop
 * 具备安装包 / 便携包要打入的最小目录结构（含 bin 内真实 L3 侧车、.env + .env.example）。
 * .env 内容：优先 JACHIN_DESKTOP_BUNDLE_ENV_FILE 或 .jachin_bundle_env_path（显式覆盖）；
 * 否则若存在仓库根 .env 则复制（与本地合并后的默认打包源）；再否则复制仓库根 .env.example。
 * 见 jachin_bundle_env_path.example。
 * 输出目录：仓库根目录 dist_jachin_desktop（与 src-tauri/tauri.conf.json 中 bundle.resources 的
 * ../../../dist_jachin_desktop/ 一致；勿使用 ../../，否则会错误指向 clients/dist_jachin_desktop）。
 * 与 scripts/build_full.ps1 第 4 步对齐；runtime/python 为可选（见环境变量）。
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { spawnSync } from "child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP = path.resolve(__dirname, "..");
const ROOT = path.resolve(DESKTOP, "..", "..");
const DIST = path.join(ROOT, "dist_jachin_desktop");

function mkdirp(p) {
  fs.mkdirSync(p, { recursive: true });
}

function copyIfExists(src, dst) {
  if (!fs.existsSync(src)) return false;
  const st = fs.statSync(src);
  if (st.isDirectory()) {
    fs.cpSync(src, dst, { recursive: true });
  } else {
    mkdirp(path.dirname(dst));
    fs.copyFileSync(src, dst);
  }
  return true;
}

/** 删除 dist bin 根目录下旧的 l3_node-* 可执行文件，避免与示例一致只保留当前 triplet 侧车（不删 bin/logs） */
/**
 * 打进 dist_jachin_desktop/.env 的来源路径（非空则必须存在）：
 * 1) JACHIN_DESKTOP_BUNDLE_ENV_FILE
 * 2) clients/desktop/.jachin_bundle_env_path（单行绝对路径，gitignore）
 * 3) 仓库根 .env（存在则作为默认）
 * 若以上均无有效路径，返回空字符串（由 main 回退为 .env.example）。
 */
function resolveBundleEnvSourcePath() {
  const fromEnv = process.env.JACHIN_DESKTOP_BUNDLE_ENV_FILE?.trim();
  if (fromEnv) return fromEnv;
  const pointer = path.join(DESKTOP, ".jachin_bundle_env_path");
  if (fs.existsSync(pointer)) {
    const raw = fs.readFileSync(pointer, "utf8");
    for (const line of raw.split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      return t;
    }
  }
  const rootEnv = path.join(ROOT, ".env");
  if (fs.existsSync(rootEnv)) return rootEnv;
  return "";
}

function pruneOldL3SidecarsInDistBin() {
  const binDir = path.join(DIST, "bin");
  if (!fs.existsSync(binDir)) return;
  for (const ent of fs.readdirSync(binDir, { withFileTypes: true })) {
    if (!ent.isFile()) continue;
    const n = ent.name;
    if (!n.startsWith("l3_node")) continue;
    if (process.platform === "win32" && !n.endsWith(".exe")) continue;
    fs.unlinkSync(path.join(binDir, n));
  }
}

/** 将 PyInstaller 侧车复制到 dist_jachin_desktop/bin，供 bundle.resources → NSIS 暴露（与 l3_spawn 的 bin/l3_node-<triple> 一致） */
function copyL3SidecarIntoDistBin() {
  const tauriBin = path.join(DESKTOP, "src-tauri", "bin");
  const candidates =
    process.platform === "win32"
      ? ["l3_node-x86_64-pc-windows-msvc.exe"]
      : process.platform === "darwin"
        ? ["l3_node-aarch64-apple-darwin", "l3_node-x86_64-apple-darwin"]
        : ["l3_node-x86_64-unknown-linux-gnu", "l3_node-aarch64-unknown-linux-gnu"];
  for (const name of candidates) {
    const src = path.join(tauriBin, name);
    if (!fs.existsSync(src)) continue;
    const st = fs.statSync(src);
    if (st.size < 64 * 1024) {
      console.warn("[prepare-installer-payload] skip tiny/placeholder sidecar:", src, `(${st.size} bytes)`);
      continue;
    }
    const dst = path.join(DIST, "bin", name);
    fs.copyFileSync(src, dst);
    console.log("[prepare-installer-payload] copied L3 sidecar ->", dst);
    return;
  }
  console.warn(
    "[prepare-installer-payload] no usable L3 sidecar under src-tauri/bin (run: python scripts/build_l3_sidecar.py or npm run build / ensure-l3-sidecar-for-bundle)"
  );
}

function main() {
  mkdirp(path.join(DIST, "config"));
  mkdirp(path.join(DIST, "scripts"));
  mkdirp(path.join(DIST, "logs"));
  fs.writeFileSync(path.join(DIST, "logs", ".gitkeep"), "", "utf8");
  mkdirp(path.join(DIST, "runtime"));

  mkdirp(path.join(DIST, "bin"));
  // 与便携包/同事示例一致：bin 内含侧车 + bin/logs（L3 运行时可写日志）
  mkdirp(path.join(DIST, "bin", "logs"));
  fs.writeFileSync(path.join(DIST, "bin", "logs", ".gitkeep"), "", "utf8");
  pruneOldL3SidecarsInDistBin();
  copyL3SidecarIntoDistBin();

  const envExample = path.join(ROOT, ".env.example");
  const rootEnv = path.join(ROOT, ".env");
  const envDst = path.join(DIST, ".env");
  const bundleEnvSrc = resolveBundleEnvSourcePath();

  if (bundleEnvSrc) {
    if (!fs.existsSync(bundleEnvSrc)) {
      console.error(
        "[prepare-installer-payload] 配置的打包用 .env 源文件不存在（请检查 JACHIN_DESKTOP_BUNDLE_ENV_FILE、clients/desktop/.jachin_bundle_env_path 或仓库根 .env）:",
        bundleEnvSrc
      );
      process.exit(1);
    }
    fs.copyFileSync(bundleEnvSrc, envDst);
    if (path.resolve(bundleEnvSrc) === path.resolve(rootEnv)) {
      console.log("[prepare-installer-payload] bundled .env from repo root .env (default when no override)");
    } else {
      console.log("[prepare-installer-payload] bundled .env from override:", bundleEnvSrc);
    }
    if (fs.existsSync(envExample)) {
      copyIfExists(envExample, path.join(DIST, ".env.example"));
    }
  } else if (fs.existsSync(envExample)) {
    copyIfExists(envExample, path.join(DIST, ".env.example"));
    fs.copyFileSync(envExample, envDst);
    console.log("[prepare-installer-payload] bundled .env from repo root .env.example (no repo .env and no override)");
  } else if (!fs.existsSync(envDst)) {
    fs.writeFileSync(
      envDst,
      "# Copy from .env.example at repo root when available.\nDASHSCOPE_API_KEY=\n",
      "utf8"
    );
  }

  const readmeSrc = fs.existsSync(path.join(ROOT, "docs", "README_DEPLOY.md"))
    ? path.join(ROOT, "docs", "README_DEPLOY.md")
    : path.join(ROOT, "README_DEPLOY.md");
  if (fs.existsSync(readmeSrc)) {
    copyIfExists(readmeSrc, path.join(DIST, "README_DEPLOY.md"));
  }

  const skillsYaml = path.join(ROOT, "config", "skills_config.yaml");
  const skillsYamlCore = path.join(ROOT, "core", "config", "skills_config.yaml");
  if (fs.existsSync(skillsYaml)) {
    copyIfExists(skillsYaml, path.join(DIST, "config", "skills_config.yaml"));
  } else if (fs.existsSync(skillsYamlCore)) {
    copyIfExists(skillsYamlCore, path.join(DIST, "config", "skills_config.yaml"));
  }

  const imEx = path.join(ROOT, "config", "im_channels.yaml.example");
  if (fs.existsSync(imEx)) {
    copyIfExists(imEx, path.join(DIST, "config", "im_channels.yaml.example"));
  }

  const recruitEx = path.join(ROOT, "config", "l3_recruitment.yaml.example");
  if (fs.existsSync(recruitEx)) {
    copyIfExists(recruitEx, path.join(DIST, "config", "l3_recruitment.yaml.example"));
  }

  const ps = path.join(ROOT, "scripts", "run_l3.ps1");
  if (fs.existsSync(ps)) {
    copyIfExists(ps, path.join(DIST, "scripts", "run_l3.ps1"));
  }
  const chromeA = path.join(ROOT, "scripts", "launch_chrome_debug.ps1");
  const chromeB = path.join(ROOT, "skills_repo", "plugin", "scripts", "launch_chrome_debug.ps1");
  if (fs.existsSync(chromeA)) {
    copyIfExists(chromeA, path.join(DIST, "scripts", "launch_chrome_debug.ps1"));
  } else if (fs.existsSync(chromeB)) {
    copyIfExists(chromeB, path.join(DIST, "scripts", "launch_chrome_debug.ps1"));
  }

  const bat = path.join(ROOT, "scripts", "run_l3.bat");
  const bat2 = path.join(ROOT, "scripts", "run_l3_standalone.bat");
  if (fs.existsSync(bat)) copyIfExists(bat, path.join(DIST, "run_l3.bat"));
  if (fs.existsSync(bat2)) copyIfExists(bat2, path.join(DIST, "run_l3_standalone.bat"));

  const pyExe = path.join(DIST, "runtime", "python", "python.exe");
  const wantMcp = process.env.JACHIN_DESKTOP_BUNDLE_MCP_RUNTIME === "1";
  if (wantMcp && !fs.existsSync(pyExe)) {
    const ps1 = path.join(ROOT, "scripts", "bundle_l3_mcp_runtime.ps1");
    if (!fs.existsSync(ps1)) {
      console.error("[prepare-installer-payload] 未找到 bundle_l3_mcp_runtime.ps1，无法嵌入 MCP runtime");
      process.exit(1);
    }
    console.log("[prepare-installer-payload] 运行 bundle_l3_mcp_runtime.ps1（JACHIN_DESKTOP_BUNDLE_MCP_RUNTIME=1）…");
    const r = spawnSync(
      "powershell",
      ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-Root", ROOT, "-OutDir", DIST],
      { stdio: "inherit", shell: true }
    );
    if (r.status !== 0) {
      process.exit(r.status ?? 1);
    }
  }

  if (!fs.existsSync(pyExe)) {
    const hint = path.join(DIST, "runtime", "README_MCP_RUNTIME_OPTIONAL.txt");
    fs.writeFileSync(
      hint,
      [
        "本目录可嵌入 MCP 所用 Python（与便携包 dist_jachin_desktop/runtime/python 一致）。",
        "若需随安装包分发：在 tauri build 前设置环境变量 JACHIN_DESKTOP_BUNDLE_MCP_RUNTIME=1，",
        "并确保可执行 scripts/bundle_l3_mcp_runtime.ps1（见 scripts/build_full.ps1 第 5 步）。",
        "",
      ].join("\r\n"),
      "utf8"
    );
  }

  // tauri.conf bundle.resources 要求存在（单文件映射）；prepare 不一定会从仓库其它路径复制到此处
  const seaDst = path.join(DIST, ".env.sea.example");
  if (!fs.existsSync(seaDst)) {
    fs.writeFileSync(
      seaDst,
      [
        "# Jachin portable / SEA-style deploy — env example (generated stub if missing).",
        "# Copy to .env and fill keys. See docs/README_DEPLOY.md.",
        "DASHSCOPE_API_KEY=",
        "",
      ].join("\n"),
      "utf8"
    );
    console.log("[prepare-installer-payload] wrote stub .env.sea.example (replace with full template if needed)");
  }

  console.log("[prepare-installer-payload] OK ->", DIST);
}

main();
