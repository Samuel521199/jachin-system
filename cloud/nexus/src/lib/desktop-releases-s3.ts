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

/**
 * 预签名 GET 使用的 S3/MinIO 地址，必须与用户浏览器最终访问的 Host 一致。
 * - 若 DESKTOP_RELEASES_S3_ENDPOINT 为 http://127.0.0.1:9000（L1 容器访问宿主机 MinIO），
 *   必须另设本项为公网可达地址（如 http://47.86.39.173:9000），否则下载链接会指向用户本机 127.0.0.1。
 * - 未设置时回退为 DESKTOP_RELEASES_S3_ENDPOINT（兼容仅公网 endpoint 的部署）。
 */
function presignEndpoint(): string {
  const pub = (process.env.DESKTOP_RELEASES_S3_PRESIGN_ENDPOINT ?? "").trim();
  if (pub) return pub;
  return (process.env.DESKTOP_RELEASES_S3_ENDPOINT ?? "").trim();
}

function getS3Client(): S3Client | null {
  if (cachedClient !== undefined) return cachedClient;
  if (!isDesktopReleasesS3Configured()) {
    cachedClient = null;
    return null;
  }
  const endpoint = presignEndpoint();
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
