/**
 * 桌面安装包私有 Bucket：仅服务端生成短效预签名 URL，避免直链被转发盗链。
 */
import { GetObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

let cachedClient: S3Client | null | undefined;

export function isDesktopReleasesS3Configured(): boolean {
  const b = (process.env.DESKTOP_RELEASES_S3_BUCKET ?? "").trim();
  const ak = (process.env.DESKTOP_RELEASES_S3_ACCESS_KEY ?? "").trim();
  const sk = (process.env.DESKTOP_RELEASES_S3_SECRET_KEY ?? "").trim();
  return !!(b && ak && sk);
}

function getS3Client(): S3Client | null {
  if (cachedClient !== undefined) return cachedClient;
  if (!isDesktopReleasesS3Configured()) {
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

export async function presignDesktopArtifactGetUrl(
  objectKey: string,
  expiresSec = 900
): Promise<string | null> {
  const client = getS3Client();
  const bucket = (process.env.DESKTOP_RELEASES_S3_BUCKET ?? "").trim();
  if (!client || !bucket) return null;
  const cmd = new GetObjectCommand({ Bucket: bucket, Key: objectKey });
  return getSignedUrl(client, cmd, { expiresIn: expiresSec });
}
