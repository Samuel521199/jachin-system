#!/usr/bin/env node
/**
 * Vite/PostCSS 在 Windows 上会 JSON.parse(package.json)；若被存成「UTF-8 带 BOM」会报 Unexpected token '﻿'。
 * 在 dev/build 前快速去掉关键 JSON 的 BOM（无则跳过）。
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const BOM = Buffer.from([0xef, 0xbb, 0xbf]);

const files = [
  "package.json",
  "package-lock.json",
  "tsconfig.json",
  "tsconfig.node.json",
  "nexus_config.example.json",
  /* Tauri 用 serde_json 读配置，带 BOM 会报 expected value at line 1 column 1 */
  "src-tauri/tauri.conf.json",
];

function stripFileIfNeeded(rel) {
  const p = path.join(root, rel);
  if (!fs.existsSync(p)) return;
  const buf = fs.readFileSync(p);
  if (buf.length < 3 || !buf.subarray(0, 3).equals(BOM)) return;
  fs.writeFileSync(p, buf.subarray(3));
  console.log("[ensure-json-no-bom] removed UTF-8 BOM from", rel);
}

for (const f of files) {
  stripFileIfNeeded(f);
}
