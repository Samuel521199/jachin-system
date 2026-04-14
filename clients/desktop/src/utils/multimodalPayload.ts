/**
 * 桌面 Omni：将 File[] 转为 L3 WebSocket attachments_metadata（与 ws_server / §12.1 对齐）
 */

export type WsAttachmentMeta = {
  name: string;
  mime: string;
  size_bytes: number;
  has_image?: boolean;
  /** data:image/...;base64,... */
  data_url?: string;
  /** 纯文本类附件正文 */
  text_content?: string;
};

const MAX_FILE_BYTES = 12 * 1024 * 1024;
const MAX_TEXT_CHARS = 200_000;

function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result ?? ""));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

function looksTextual(file: File): boolean {
  const t = (file.type || "").toLowerCase();
  if (
    t.startsWith("text/") ||
    t === "application/json" ||
    t === "application/xml" ||
    t === "application/x-yaml"
  ) {
    return true;
  }
  return /\.(txt|md|csv|json|log|yaml|yml|xml)$/i.test(file.name);
}

/**
 * 单文件 → 元数据；过大或失败返回 null（调用方可跳过并提示）
 */
export async function fileToWsAttachmentMeta(file: File): Promise<WsAttachmentMeta | null> {
  if (file.size > MAX_FILE_BYTES) return null;
  const name = file.name || "unnamed";
  const mime = file.type || "application/octet-stream";

  if (mime.startsWith("image/")) {
    const data_url = await readFileAsDataURL(file);
    return {
      name,
      mime,
      size_bytes: file.size,
      has_image: true,
      data_url,
    };
  }

  if (looksTextual(file)) {
    let text = await file.text();
    if (text.length > MAX_TEXT_CHARS) text = `${text.slice(0, MAX_TEXT_CHARS)}\n…[已截断]`;
    return {
      name,
      mime,
      size_bytes: file.size,
      has_image: false,
      text_content: text,
    };
  }

  if (mime === "application/pdf" || /\.pdf$/i.test(name)) {
    return {
      name,
      mime: "application/pdf",
      size_bytes: file.size,
      has_image: false,
      text_content: `【PDF「${name}」已随消息上传；当前链路优先解析图片与纯文本。如需分析 PDF 请粘贴关键页文字或使用专用工具。】`,
    };
  }

  return {
    name,
    mime,
    size_bytes: file.size,
    has_image: false,
    text_content: `【已引用附件「${name}」（${mime || "未知类型"}）。请在对话中说明需要如何处理。】`,
  };
}

export async function filesToAttachmentsMetadata(files: File[]): Promise<WsAttachmentMeta[]> {
  const out: WsAttachmentMeta[] = [];
  for (const f of files) {
    try {
      const m = await fileToWsAttachmentMeta(f);
      if (m) out.push(m);
    } catch {
      /* 单文件失败则跳过 */
    }
  }
  return out;
}

/** 气泡展示：正文 + 附件摘要行 */
export function formatUserBubbleLine(userText: string, meta: WsAttachmentMeta[]): string {
  const t = (userText || "").trim();
  if (!meta.length) return t;
  const names = meta.map((m) => m.name).join("、");
  return (t || "📎") + `\n〈${meta.length} 个附件：${names}〉`;
}

/** L2 纯文本兜底：无 vision 时将附件摘要拼进 user 字符串 */
export function buildL2FallbackUserText(userText: string, meta: WsAttachmentMeta[]): string {
  const t = (userText || "").trim();
  if (!meta.length) return t;
  const lines = meta.map((m) => {
    if (m.data_url) return `- [图片] ${m.name} (${m.mime}, ${m.size_bytes} B)`;
    const tx = (m.text_content || "").trim();
    const head = tx.length > 6000 ? `${tx.slice(0, 6000)}\n…` : tx;
    return `- [文件] ${m.name}\n${head}`;
  });
  return `${t ? `${t}\n\n` : ""}【附件】\n${lines.join("\n\n")}`;
}
