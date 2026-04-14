/**
 * Windows 上若 JSON 被存成「UTF-8 带 BOM」，Node/tsx/Next/webpack 会 JSON.parse 失败（Unexpected token '﻿'）。
 * 覆盖：package.json、lockfile、tsconfig、eslint、drizzle/meta/*.json
 * db:migrate / dev / db:init-store 等脚本会先执行本文件，start-cloud.ps1 也会在 npm 前调用。
 */
const fs = require("fs");
const path = require("path");

const BOM = Buffer.from([0xef, 0xbb, 0xbf]);
const root = path.join(__dirname, "..");

const files = [
  "package.json",
  "package-lock.json",
  "tsconfig.json",
  ".eslintrc.json",
];

function stripPath(absPath, displayRel) {
  if (!fs.existsSync(absPath)) return false;
  const buf = fs.readFileSync(absPath);
  if (buf.length < 3 || !buf.subarray(0, 3).equals(BOM)) return false;
  fs.writeFileSync(absPath, buf.subarray(3));
  console.log("[ensure-json-no-bom] removed UTF-8 BOM from", displayRel);
  return true;
}

function stripFile(rel) {
  return stripPath(path.join(root, rel), rel);
}

function stripDirJson(dirRel) {
  const dir = path.join(root, dirRel);
  if (!fs.existsSync(dir)) return;
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".json")) continue;
    const abs = path.join(dir, name);
    stripPath(abs, path.posix.join(dirRel.replace(/\\/g, "/"), name));
  }
}

for (const f of files) {
  stripFile(f);
}
stripDirJson("drizzle/meta");
