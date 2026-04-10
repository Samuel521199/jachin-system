import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import { and, desc, eq, sql } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import { error as logError } from "@/lib/console-utc";
import { listOrganizationsForUser } from "@/lib/org-membership-db";
import { resolveOrganizationForL2Gateway } from "@/lib/l1-workspace-context";
import { generatePairingShortCode } from "@/lib/pairing-code-util";
import { edgeAgents, users } from "@/db/schema";
import { passwordPlainForCredentials } from "@/lib/auth/credentials-password";
import { credentialsHashUsable } from "@/lib/auth/password-hash";

export const dynamic = "force-dynamic";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function nexusBaseUrl(): string {
  return (
    process.env.NEXUS_PUBLIC_URL ||
    (process.env.NEXT_PUBLIC_VERCEL_URL
      ? `https://${process.env.NEXT_PUBLIC_VERCEL_URL}`
      : "http://localhost:3000")
  ).replace(/\/$/, "");
}

/**
 * POST /api/v1/l2-gateway/verify-credentials
 * 供 L2 服务端使用：校验 L1 注册邮箱 + 密码，返回与 l2-bridge/redeem 同构的配对字段，便于写入 nexus_config。
 *
 * 可选：L1 设置 L1_L2_LOGIN_SHARED_SECRET 时，请求须带 Header X-L2-Gateway-Secret（与 L2 的 NEXUS_L2_LOGIN_SECRET 一致）。
 */
export async function POST(req: NextRequest) {
  const shared = (process.env.L1_L2_LOGIN_SHARED_SECRET || "").trim();
  if (shared) {
    const sent = (req.headers.get("x-l2-gateway-secret") || "").trim();
    if (sent !== shared) {
      return NextResponse.json(
        {
          success: false,
          error: "FORBIDDEN",
          message: "无效的服务端密钥",
        },
        { status: 403 }
      );
    }
  }

  let body: { email?: string; password?: string; organization_id?: string };
  try {
    body = (await req.json()) as {
      email?: string;
      password?: string;
      organization_id?: string;
    };
  } catch {
    return NextResponse.json(
      { success: false, error: "BAD_REQUEST", message: "Invalid JSON" },
      { status: 400 }
    );
  }

  const email =
    typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  const password = passwordPlainForCredentials(body.password);

  if (!email || !EMAIL_RE.test(email) || !password) {
    return NextResponse.json(
      {
        success: false,
        error: "INVALID_INPUT",
        message: "请输入有效邮箱与密码",
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

  const [u] = await db
    .select({
      id: users.id,
      passwordHash: users.passwordHash,
    })
    .from(users)
    .where(sql`lower(trim(${users.email})) = ${email}`)
    .limit(1);

  if (!u) {
    return NextResponse.json(
      { success: false, error: "AUTH_FAILED", message: "邮箱或密码错误" },
      { status: 401 }
    );
  }

  const storedHash = (u.passwordHash ?? "").trim();
  if (!credentialsHashUsable(storedHash)) {
    return NextResponse.json(
      {
        success: false,
        error: "PASSWORD_NOT_SET",
        message:
          "该账号仅支持 OAuth 或未设置密码，请在 L1 设置密码或使用「Nexus 账号登录」网页授权",
      },
      { status: 401 }
    );
  }

  const ok = await bcrypt.compare(password, storedHash);
  if (!ok) {
    return NextResponse.json(
      { success: false, error: "AUTH_FAILED", message: "邮箱或密码错误" },
      { status: 401 }
    );
  }

  const orgRows = await listOrganizationsForUser(db, u.id);
  if (!orgRows.length) {
    return NextResponse.json(
      {
        success: false,
        error: "WORKSPACE_REQUIRED",
        message:
          "尚未加入任何工作区。请先在 L1 控制台「工作区」创建或加入组织，再使用 L1 邮箱登录 L2 网关。",
      },
      { status: 403 }
    );
  }

  const explicitOrg =
    typeof body.organization_id === "string" ? body.organization_id.trim() : "";
  const gatewayOrg = await resolveOrganizationForL2Gateway(
    db,
    u.id,
    explicitOrg || undefined
  );
  if (!gatewayOrg) {
    return NextResponse.json(
      {
        success: false,
        error: "L2_GATEWAY_ROLE_REQUIRED",
        message:
          "仅工作区的所有者或管理员可使用邮箱密码登录 L2 网关。普通成员请让管理员操作，或在请求中指定你有管理权限的 organization_id。",
      },
      { status: 403 }
    );
  }

  const orgId = gatewayOrg.orgId;

  const [existing] = await db
    .select({
      id: edgeAgents.id,
      authToken: edgeAgents.authToken,
      organizationId: edgeAgents.organizationId,
    })
    .from(edgeAgents)
    .where(and(eq(edgeAgents.userId, u.id), eq(edgeAgents.status, "active")))
    .orderBy(desc(edgeAgents.updatedAt))
    .limit(1);

  let instanceId: string;
  let accessToken: string;

  if (existing?.id && existing.authToken) {
    instanceId = existing.id;
    accessToken = existing.authToken;
    if (orgId && existing.organizationId !== orgId) {
      await db
        .update(edgeAgents)
        .set({ organizationId: orgId, updatedAt: new Date() })
        .where(eq(edgeAgents.id, instanceId));
    }
  } else {
    accessToken = `jch-${randomUUID().replace(/-/g, "")}`;
    let inserted = false;
    instanceId = "";
    for (let retry = 0; retry < 5 && !inserted; retry++) {
      const shortCode = generatePairingShortCode();
      try {
        const [row] = await db
          .insert(edgeAgents)
          .values({
            pairingCode: shortCode,
            status: "active",
            name: `L2-Gateway-${shortCode}`,
            userId: u.id,
            organizationId: orgId,
            authToken: accessToken,
            pairingExpiresAt: null,
          })
          .returning({ id: edgeAgents.id });
        instanceId = row?.id ?? "";
        inserted = !!instanceId;
      } catch (e: unknown) {
        const err = e as { code?: string };
        if (err?.code === "23505") continue;
        logError("[l2-gateway/verify-credentials] DB insert error:", e);
        return NextResponse.json(
          {
            success: false,
            error: "AGENT_CREATE_FAILED",
            message: "创建边缘实例失败",
          },
          { status: 500 }
        );
      }
    }
    if (!inserted || !instanceId) {
      return NextResponse.json(
        {
          success: false,
          error: "AGENT_CREATE_FAILED",
          message: "无法分配边缘实例，请重试",
        },
        { status: 500 }
      );
    }
  }

  const baseUrl = nexusBaseUrl();

  return NextResponse.json({
    success: true,
    l1_user_id: u.id,
    tenant_id: orgId,
    workspace_admin: true,
    organization_role: gatewayOrg.role,
    access_token: accessToken,
    instance_id: instanceId,
    nexus_base_url: baseUrl,
  });
}
