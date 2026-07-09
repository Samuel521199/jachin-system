import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import esbuild from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const entry = path.join(root, "src", "components", "Chat", "pendingConfirmationProtocol.ts");
const tmp = await mkdtemp(path.join(tmpdir(), "jachin-pending-confirmation-"));
const outfile = path.join(tmp, "pendingConfirmationProtocol.mjs");

try {
  await esbuild.build({
    entryPoints: [entry],
    outfile,
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node18",
    logLevel: "silent",
  });

  const mod = await import(pathToFileURL(outfile).href);
  const confirmText = "\u786e\u8ba4\u6267\u884c";
  const cancelText = "\u53d6\u6d88";
  const payload = {
    type: "pending_confirmation",
    decision_id: "decision_app_open_1",
    work_order_id: "wo_app_open_1",
    tool: "app.open",
    risk: "high",
    confirm_text: confirmText,
    cancel_text: cancelText,
  };
  const raw = [
    "\u6211\u9700\u8981\u786e\u8ba4\u540e\u518d\u6267\u884c\uff1a\u6253\u5f00\u8ba1\u7b97\u5668\u3002",
    `<!-- jachin-ui:pending-confirmation ${JSON.stringify(payload)} -->`,
  ].join("\n");

  const protocol = mod.extractPendingConfirmationProtocol(raw);
  assert.equal(protocol?.decision_id, payload.decision_id);
  assert.equal(protocol?.work_order_id, payload.work_order_id);
  assert.equal(protocol?.confirm_text, confirmText);
  assert.equal(protocol?.cancel_text, cancelText);

  const stripped = mod.stripAssistantUiProtocol(raw);
  assert.equal(stripped.includes("jachin-ui:pending-confirmation"), false);
  assert.equal(stripped.includes("\u6253\u5f00\u8ba1\u7b97\u5668"), true);
  assert.equal(mod.shouldShowMissionConfirmationControls(stripped, protocol), true);
  assert.deepEqual(mod.pendingConfirmationQuickReplies(protocol), {
    confirmText,
    cancelText,
  });

  assert.equal(
    mod.shouldShowMissionConfirmationControls(
      "Task Preview: Codex project briefing to Lark\n\u786e\u8ba4\u540e\u6211\u518d\u6267\u884c\uff0c\u4e5f\u53ef\u4ee5\u53d6\u6d88\u3002",
      null,
    ),
    true,
  );

  console.log(
    JSON.stringify(
      {
        ok: true,
        checked: [
          "protocol_parse",
          "protocol_strip",
          "button_visibility",
          "confirm_cancel_quick_replies",
          "legacy_task_preview_fallback",
        ],
      },
      null,
      2,
    ),
  );
} finally {
  await rm(tmp, { recursive: true, force: true });
}
