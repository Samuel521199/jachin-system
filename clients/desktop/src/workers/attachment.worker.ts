/**
 * 附件读取与 Base64 编码在独立线程执行，避免阻塞主线程 UI。
 */
/// <reference lib="webworker" />

import {
  buildAttachmentsMetadataPayloadCore,
  type BuildAttachmentsResult,
} from "../utils/attachmentPayloadCore";

export type AttachmentWorkerInbound = {
  id: number;
  files: File[];
};

export type AttachmentWorkerOutbound = {
  id: number;
  result: BuildAttachmentsResult;
};

self.onmessage = async (e: MessageEvent<AttachmentWorkerInbound>) => {
  const { id, files } = e.data;
  try {
    const result = await buildAttachmentsMetadataPayloadCore(files);
    const out: AttachmentWorkerOutbound = { id, result };
    self.postMessage(out);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const out: AttachmentWorkerOutbound = {
      id,
      result: { ok: false, error: msg || "读取附件失败，请重试" },
    };
    self.postMessage(out);
  }
};
