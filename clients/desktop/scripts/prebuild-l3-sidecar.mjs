import { spawnSync } from "node:child_process";

if (process.env.JACHIN_SKIP_L3_PREBUILD === "1") {
  console.log("[prebuild-l3-sidecar] skipped by JACHIN_SKIP_L3_PREBUILD=1");
  process.exit(0);
}

const result = spawnSync("npm", ["run", "build:l3-sidecar"], {
  cwd: process.cwd(),
  shell: true,
  stdio: "inherit",
});

process.exit(result.status ?? 1);
