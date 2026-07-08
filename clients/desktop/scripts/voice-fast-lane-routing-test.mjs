import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { createRequire } from "node:module";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(desktopRoot, "..", "..");
const routerPath = path.join(desktopRoot, "src", "voice", "voiceIntentRouter.ts");
const requireForVm = createRequire(import.meta.url);

function loadRouter() {
  const source = fs.readFileSync(routerPath, "utf8");
  const js = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
  }).outputText;
  const sandbox = { exports: {}, require: requireForVm, console };
  vm.runInNewContext(js, sandbox, { filename: "voiceIntentRouter.js" });
  if (typeof sandbox.exports.dispatchVoiceIntent !== "function") {
    throw new Error("dispatchVoiceIntent export not found");
  }
  return sandbox.exports.dispatchVoiceIntent;
}

function assertEqual(results, name, actual, expected, detail) {
  const passed = actual === expected;
  results.push({ name, passed, detail: `${detail}: actual=${JSON.stringify(actual)} expected=${JSON.stringify(expected)}` });
}

function summarize(results) {
  for (const r of results) {
    console.log(`[${r.passed ? "PASS" : "FAIL"}] ${r.name} -> ${r.detail}`);
  }
  const failed = results.filter((r) => !r.passed);
  console.log(`\nSummary: ${results.length - failed.length}/${results.length} checks passed.`);
  if (failed.length) {
    process.exitCode = 1;
  }
}

function runFrontendRouterChecks() {
  const dispatchVoiceIntent = loadRouter();
  const cases = [
    {
      name: "presence: hello",
      text: "\u4f60\u597d",
      expect: { tier: "CHIT_CHAT", intent: "CHITCHAT", lane: "direct_llm", kind: "presence_template", template: true, fast: true },
    },
    {
      name: "presence: are you there",
      text: "\u5728\u5417",
      expect: { tier: "CHIT_CHAT", intent: "CHITCHAT", lane: "direct_llm", kind: "presence_template", template: true, fast: true },
    },
    {
      name: "light query: what to eat",
      text: "\u4eca\u5929\u5403\u4ec0\u4e48",
      expect: { tier: "CHIT_CHAT", intent: "QUERY_LIGHT", lane: "direct_llm", kind: "light_query", template: false, fast: true },
    },
    {
      name: "light query: opinion",
      text: "\u4f60\u89c9\u5f97\u6211\u4eca\u5929\u5403\u4ec0\u4e48",
      expect: { tier: "CHIT_CHAT", intent: "QUERY_LIGHT", lane: "direct_llm", kind: "light_query", template: false, fast: true },
    },
    {
      name: "short task: calculator",
      text: "\u5e2e\u6211\u6253\u5f00\u8ba1\u7b97\u5668",
      expect: { tier: "SHORT_TASK", intent: "TASK_SYNC", lane: "foreground", kind: "none", template: false, fast: false },
    },
    {
      name: "long task: report",
      text: "\u628a\u6574\u4e2a\u76ee\u5f55\u751f\u6210\u62a5\u544a",
      expect: { tier: "LONG_TASK", intent: "TASK_ASYNC", lane: "background_submit", kind: "none", template: false, fast: false },
    },
  ];

  const results = [];
  console.log("\n=== Frontend voiceIntentRouter checks ===");
  for (const c of cases) {
    const d = dispatchVoiceIntent(c.text, { activeTasks: [] });
    const got = {
      tier: d.tier,
      intent: d.intent_class,
      lane: d.execution_lane,
      kind: d.router_hints.fast_lane_kind,
      template: d.router_hints.allow_template_reply,
      fast: d.router_hints.fast_lane,
    };
    console.log(`\n[CASE] ${c.name}`);
    console.log(JSON.stringify({ text: c.text, got, route_notes: d.route_notes, evidence: d.route_evidence }, null, 2));
    for (const [key, expected] of Object.entries(c.expect)) {
      assertEqual(results, `${c.name}.${key}`, got[key], expected, key);
    }
  }
  return results;
}

function runServerTemplateGateChecks() {
  const py = String.raw`
from l3_node import ws_server

cases = [
    ("presence template allowed", "\u4f60\u597d", {"voice_fast_lane_kind": "presence_template", "voice_allow_template_reply": True, "voice_raw_stt_text": "\u4f60\u597d"}, True),
    ("light query template blocked", "\u4eca\u5929\u5403\u4ec0\u4e48", {"voice_fast_lane_kind": "light_query", "voice_allow_template_reply": False, "voice_raw_stt_text": "\u4eca\u5929\u5403\u4ec0\u4e48"}, False),
    ("chat direct template blocked", "\u8bf4\u70b9\u8bdd", {"voice_fast_lane_kind": "chat_direct", "voice_allow_template_reply": False, "voice_raw_stt_text": "\u8bf4\u70b9\u8bdd"}, False),
]

for name, text, sig, should_have_template in cases:
    reply = ws_server._pick_ws_voice_template_reply(text, sig)
    ok = bool(reply) == should_have_template
    safe_reply = (reply or "").encode("unicode_escape").decode("ascii")
    print(("PASS" if ok else "FAIL") + "\t" + name + "\t" + ("template=" + safe_reply))
`;
  const result = spawnSync("python", ["-c", py], {
    cwd: repoRoot,
    encoding: "utf8",
  });
  const checks = [];
  console.log("\n=== L3 ws_server template gate checks ===");
  if (result.error) {
    checks.push({ name: "python subprocess", passed: false, detail: String(result.error) });
    return checks;
  }
  if (result.stderr.trim()) {
    console.error(result.stderr.trim());
  }
  for (const line of result.stdout.trim().split(/\r?\n/).filter(Boolean)) {
    console.log(line);
    const [status, name, detail = ""] = line.split("\t");
    checks.push({ name: `server.${name}`, passed: status === "PASS", detail });
  }
  if (result.status !== 0) {
    checks.push({ name: "python exit code", passed: false, detail: `status=${result.status}` });
  }
  return checks;
}

const allResults = [
  ...runFrontendRouterChecks(),
  ...runServerTemplateGateChecks(),
];

summarize(allResults);
