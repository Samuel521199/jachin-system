import { NextRequest, NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import { organizationUsers, users } from "@/db/schema";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/l2-gateway/workspace-members?organization_id=<uuid>
 *
 * L2 服务端凭 L1_L2_LOGIN_SHARED_SECRET 拉取某工作区成员列表（只读），用于 /gateway 展示。
 * 与 verify-credentials 使用同一共享密钥，**不**面向浏览器匿名访问。
 */
export async function GET(req: NextRequest) {
  const shared = (process.env.L1_L2_LOGIN_SHARED_SECRET || "").trim();
  if (shared) {
    const sent = (req.headers.get("x-l2-gateway-secret") || "").trim();
    if (sent !== shared) {
      return NextResponse.json(
        { success: false, error: "FORBIDDEN", message: "无效的服务端密钥" },
        { status: 403 }
      );
    }
  } else {
    return NextResponse.json(
      {
        success: false,
        error: "CONFIG",
        message: "未配置 L1_L2_LOGIN_SHARED_SECRET，拒绝服务端拉取成员列表",
      },
      { status: 503 }
    );
  }

  const organizationId =
    new URL(req.url).searchParams.get("organization_id")?.trim() ?? "";
  if (!organizationId) {
    return NextResponse.json(
      {
        success: false,
        error: "BAD_REQUEST",
        message: "缺少 organization_id",
      },
      { status: 400 }
    );
  }

  if (!isDatabaseConfigured()) {
    return NextResponse.json(
      {
        success: false,
        error: "DATABASE_UNAVAILABLE",
        message: "未配置 DATABASE_URL",
      },
      { status: 503 }
    );
  }

  const db = getDb()!;
  const rows = await db
    .select({
      userId: organizationUsers.userId,
      role: organizationUsers.role,
      email: users.email,
      name: users.name,
      joinedAt: organizationUsers.createdAt,
    })
    .from(organizationUsers)
    .innerJoin(users, eq(users.id, organizationUsers.userId))
    .where(eq(organizationUsers.orgId, organizationId));

  return NextResponse.json({
    success: true,
    data: {
      organization_id: organizationId,
      members: rows.map((r) => ({
        user_id: r.userId,
        role: r.role,
        email: r.email,
        name: r.name,
        joined_at: r.joinedAt?.toISOString() ?? null,
      })),
      total: rows.length,
    },
  });
}
