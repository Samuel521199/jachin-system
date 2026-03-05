/**
 * IPFS 上传 - 星际文件系统分发
 * 支持 Pinata、Web3.Storage，实现「只管协议，不碰数据」的去中心化承诺
 */

const PINATA_UPLOAD_URL = "https://uploads.pinata.cloud/v3/files";

export interface IpfsUploadResult {
  cid: string;
  url: string; // ipfs://{cid}
}

/**
 * 检查 IPFS 是否已配置（Pinata JWT）
 */
export function isIpfsConfigured(): boolean {
  return Boolean(process.env.PINATA_JWT);
}

/**
 * 上传文件至 IPFS（Pinata）
 * 返回 ipfs://{cid} 格式的不可篡改内容标识
 */
export async function uploadToIpfs(
  fileBuffer: Buffer,
  filename: string,
  contentType = "application/zip"
): Promise<IpfsUploadResult | null> {
  const jwt = process.env.PINATA_JWT;
  if (!jwt) {
    console.warn("PINATA_JWT 未配置，跳过 IPFS 上传");
    return null;
  }

  try {
    const formData = new FormData();
    const blob = new Blob([new Uint8Array(fileBuffer)], { type: contentType });
    const file = new File([blob], filename, { type: contentType });
    formData.append("file", file);
    formData.append("network", "public");

    const resp = await fetch(PINATA_UPLOAD_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${jwt}`,
      },
      body: formData,
    });

    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`Pinata upload failed: ${resp.status} ${errText}`);
    }

    const data = (await resp.json()) as { cid?: string; data?: { cid?: string } };
    const cid = data.cid ?? data.data?.cid;
    if (!cid) {
      throw new Error("Pinata response missing cid");
    }

    const url = `ipfs://${cid}`;
    console.log(`✅ 武器包已推入星际文件系统: ${url}`);
    return { cid, url };
  } catch (e) {
    console.error("IPFS 上传失败:", e);
    return null;
  }
}
