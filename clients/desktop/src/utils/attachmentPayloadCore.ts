/**
 * 附件载荷纯逻辑（可在 Web Worker 与主线程共用），与 multimodal_attachments.py 对齐。
 */

/** 单次对话（一轮发送）最多附件个数 */
export const OMNI_ATTACHMENT_MAX_FILES = 5;
/** 单个文件大小上限 */
export const OMNI_ATTACHMENT_MAX_FILE_BYTES = 5 * 1024 * 1024;
/** 一轮内附件总大小上限（= 个数 × 单文件上限，便于与后端一致） */
export const OMNI_ATTACHMENT_MAX_BATCH_BYTES = OMNI_ATTACHMENT_MAX_FILES * OMNI_ATTACHMENT_MAX_FILE_BYTES;

const EXT_OK = new Set(["pdf", "doc", "docx", "xlsx", "xls", "txt", "md", "csv", "log"]);

export function isAllowedAttachmentFile(file: File): boolean {
  const t = (file.type || "").toLowerCase();
  if (t.startsWith("image/")) return true;
  if (
    t === "application/pdf" ||
    t === "application/msword" ||
    t === "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
    t === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" ||
    t === "application/vnd.ms-excel" ||
    t === "text/plain"
  ) {
    return true;
  }
  const ext = file.name.includes(".") ? file.name.split(".").pop()?.toLowerCase() ?? "" : "";
  return EXT_OK.has(ext);
}

export function totalBytes(files: File[]): number {
  return files.reduce((s, f) => s + f.size, 0);
}

/** 原始 Base64（无 data: 前缀），与后端 `base64` 字段一致 */
export function fileToBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const dataUrl = r.result as string;
      const i = dataUrl.indexOf(",");
      resolve(i >= 0 ? dataUrl.slice(i + 1) : "");
    };
    r.onerror = () => reject(new Error("file_read_failed"));
    r.readAsDataURL(file);
  });
}

export type AttachmentMetadataPayload = {
  name: string;
  size_bytes: number;
  mime: string;
  has_image: boolean;
  base64: string;
};

export type BuildAttachmentsResult =
  | { ok: true; items: AttachmentMetadataPayload[] }
  | { ok: false; error: string };

/**
 * 校验总体积与类型，并并行转 Base64（供 Worker 内调用，行为与历史主线程实现一致）。
 */
export async function buildAttachmentsMetadataPayloadCore(files: File[]): Promise<BuildAttachmentsResult> {
  if (files.length === 0) return { ok: true, items: [] };
  if (files.length > OMNI_ATTACHMENT_MAX_FILES) {
    return {
      ok: false,
      error: `每次最多 ${OMNI_ATTACHMENT_MAX_FILES} 个附件，请移除多余文件后重试`,
    };
  }
  const rejected = files.filter((f) => !isAllowedAttachmentFile(f));
  if (rejected.length > 0) {
    return {
      ok: false,
      error: `不支持的格式：${rejected.map((f) => f.name).join(", ")}（允许图片、PDF、Word、Excel、TXT）`,
    };
  }
  const oversized = files.filter((f) => f.size > OMNI_ATTACHMENT_MAX_FILE_BYTES);
  if (oversized.length > 0) {
    return {
      ok: false,
      error: `单文件须 ≤ ${OMNI_ATTACHMENT_MAX_FILE_BYTES / 1024 / 1024}MB：${oversized.map((f) => f.name).join(", ")}`,
    };
  }
  const sum = totalBytes(files);
  if (sum > OMNI_ATTACHMENT_MAX_BATCH_BYTES) {
    return {
      ok: false,
      error: `附件总大小超过 ${Math.floor(OMNI_ATTACHMENT_MAX_BATCH_BYTES / 1024 / 1024)}MB，请减少文件或分批发送`,
    };
  }
  try {
    const items: AttachmentMetadataPayload[] = await Promise.all(
      files.map(async (file) => {
        const base64 = await fileToBase64(file);
        const mime = file.type || "application/octet-stream";
        return {
          name: file.name,
          size_bytes: file.size,
          mime,
          has_image: mime.startsWith("image/"),
          base64,
        };
      }),
    );
    return { ok: true, items };
  } catch {
    return { ok: false, error: "读取附件失败，请重试" };
  }
}
