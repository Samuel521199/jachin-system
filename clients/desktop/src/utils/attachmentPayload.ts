/**
 * Omni 附件 → L3 WebSocket `attachments_metadata`（与 multimodal_attachments.py 对齐）
 * 重 CPU 路径（读文件 + Base64 + 组装）在 Web Worker 中执行，主线程仅做 Promise 等待与派发。
 */

import type { BuildAttachmentsResult } from "./attachmentPayloadCore";
import {
  OMNI_ATTACHMENT_MAX_BATCH_BYTES,
  OMNI_ATTACHMENT_MAX_FILE_BYTES,
  OMNI_ATTACHMENT_MAX_FILES,
  isAllowedAttachmentFile,
  totalBytes,
} from "./attachmentPayloadCore";

export {
  OMNI_ATTACHMENT_MAX_BATCH_BYTES,
  OMNI_ATTACHMENT_MAX_FILE_BYTES,
  OMNI_ATTACHMENT_MAX_FILES,
  isAllowedAttachmentFile,
  totalBytes,
} from "./attachmentPayloadCore";

export type {
  AttachmentMetadataPayload,
  BuildAttachmentsResult,
} from "./attachmentPayloadCore";

export { fileToBase64 } from "./attachmentPayloadCore";

export type MergePendingFilesResult = { next: File[]; hint: string | null };

/**
 * 将新选择的文件合并进待发列表（类型、个数、单文件大小、总大小）。
 * 供拖放 / 文件选择共用。
 */
export function mergePendingAttachmentFiles(prev: File[], incoming: File[]): MergePendingFilesResult {
  const hints: string[] = [];

  const badType = incoming.filter((f) => !isAllowedAttachmentFile(f));
  const goodType = incoming.filter((f) => isAllowedAttachmentFile(f));
  if (badType.length) {
    hints.push(`已跳过不支持的类型：${badType.map((f) => f.name).join(", ")}`);
  }
  if (!goodType.length) {
    return { next: prev, hint: hints.length ? hints.join("；") : null };
  }

  if (prev.length >= OMNI_ATTACHMENT_MAX_FILES) {
    hints.push(`每次最多 ${OMNI_ATTACHMENT_MAX_FILES} 个附件，请先移除后再添加`);
    return { next: prev, hint: hints.join("；") };
  }

  const tooBig = goodType.filter((f) => f.size > OMNI_ATTACHMENT_MAX_FILE_BYTES);
  const sizeOk = goodType.filter((f) => f.size <= OMNI_ATTACHMENT_MAX_FILE_BYTES);
  if (tooBig.length) {
    hints.push(
      `单文件须 ≤ ${OMNI_ATTACHMENT_MAX_FILE_BYTES / 1024 / 1024}MB，已跳过：${tooBig.map((f) => f.name).join(", ")}`,
    );
  }

  const toAdd: File[] = [];
  for (const f of sizeOk) {
    if (prev.length + toAdd.length >= OMNI_ATTACHMENT_MAX_FILES) break;
    const candidate = [...prev, ...toAdd, f];
    if (totalBytes(candidate) > OMNI_ATTACHMENT_MAX_BATCH_BYTES) {
      hints.push("附件总大小已达上限，后续文件未添加");
      break;
    }
    toAdd.push(f);
  }

  const skippedAfter = sizeOk.slice(toAdd.length);
  if (skippedAfter.length) {
    hints.push(`未添加：${skippedAfter.map((f) => f.name).join(", ")}`);
  }

  if (!toAdd.length) {
    return { next: prev, hint: hints.length ? hints.join("；") : null };
  }
  return { next: [...prev, ...toAdd], hint: hints.length ? hints.join("；") : null };
}

let _workerSeq = 0;

function runAttachmentWorker(files: File[]): Promise<BuildAttachmentsResult> {
  return new Promise((resolve) => {
    const id = ++_workerSeq;
    const worker = new Worker(new URL("../workers/attachment.worker.ts", import.meta.url), {
      type: "module",
    });
    const finish = (result: BuildAttachmentsResult) => {
      worker.onmessage = null;
      worker.onerror = null;
      worker.terminate();
      resolve(result);
    };
    worker.onmessage = (ev: MessageEvent<{ id: number; result: BuildAttachmentsResult }>) => {
      const data = ev.data;
      if (!data || data.id !== id) return;
      finish(data.result);
    };
    worker.onerror = (evt) => {
      evt.preventDefault();
      finish({
        ok: false,
        error: evt.message || "读取附件失败，请重试",
      });
    };
    try {
      worker.postMessage({ id, files });
    } catch {
      finish({ ok: false, error: "读取附件失败，请重试" });
    }
  });
}

/**
 * 校验总体积与类型，并并行转 Base64（在 Web Worker 内执行，不阻塞主线程）。
 */
export async function buildAttachmentsMetadataPayload(files: File[]): Promise<BuildAttachmentsResult> {
  if (files.length === 0) return { ok: true, items: [] };
  return runAttachmentWorker(files);
}
