/**
 * MinIO / S3 兼容：私有 Bucket 仅通过预签名 GET 下发安装包。
 * 环境变量与 Nexus `DESKTOP_RELEASES_S3_*` 对齐，便于共用同一套存储。
 */
import { GetObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

let cachedClient: S3Client | null | undefined;

export function isS3Configured(): boolean {
  const b = (process.env.DESKTOP_RELEASES_S3_BUCKET ?? "").trim();
  const ak = (process.env.DESKTOP_RELEASES_S3_ACCESS_KEY ?? "").trim();
  const sk = (process.env.DESKTOP_RELEASES_S3_SECRET_KEY ?? "").trim();
  return !!(b && ak && sk);
}

function getClient(): S3Client | null {
  if (cachedClient !== undefined) return cachedClient;
  if (!isS3Configured()) {
    cachedClient = null;
    return null;
  }
  const endpoint = (process.env.DESKTOP_RELEASES_S3_ENDPOINT ?? "").trim();
  const region = (process.env.DESKTOP_RELEASES_S3_REGION ?? "us-east-1").trim();
  cachedClient = new S3Client({
    region,
    endpoint: endpoint || undefined,
    credentials: {
      accessKeyId: process.env.DESKTOP_RELEASES_S3_ACCESS_KEY!,
      secretAccessKey: process.env.DESKTOP_RELEASES_S3_SECRET_KEY!,
    },
    forcePathStyle: (process.env.DESKTOP_RELEASES_S3_FORCE_PATH_STYLE ?? "true")
      .toLowerCase()
      .trim() !== "false",
  });
  return cachedClient;
}

/**
 * 生成限时下载 URL（默认 15 分钟）。
 */
export async function getDownloadUrl(
  objectKey: string,
  expiresIn = 900
): Promise<string | null> {
  const client = getClient();
  const bucket = (process.env.DESKTOP_RELEASES_S3_BUCKET ?? "").trim();
  if (!client || !bucket) return null;
  const cmd = new GetObjectCommand({ Bucket: bucket, Key: objectKey });
  return getSignedUrl(client, cmd, { expiresIn });
}
