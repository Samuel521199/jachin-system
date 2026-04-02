import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth";
import { getDb, isDatabaseConfigured } from "@/db";
import {
  listOrganizationsForUser,
} from "@/lib/org-membership-db";
import {
  pickSessionDefaultOrg,
  userCanManageL2Gateway,
} from "@/lib/l1-workspace-context";
import { error as logError } from "@/lib/console-utc";
import {
  generateBridgeCode,
  l2BridgeStoreSet,
} from "@/lib/l2-bridge-store";
import { validateL2BridgeReturnTo } from "@/lib/l2-bridge-return-to";

export const dynamic = "force-dynamic";

const TTL_SEC = 600;

/**
 * POST /api/v1/l2-bridge/mint
 * Body: { return_to?: string } — 若提供则须匹配 L2_BRIDGE_ALLOWED_RETURN_PREFIXES（防 open redirect）。
 * 已登录 L1 用户生成一次性 bridge_code，供 L2 服务端 redeem 后写入 nexus_config。
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const returnTo =
      typeof body.return_to === "string" ? body.return_to.trim() : "";
    if (!returnTo) {
      return NextResponse.json(
        {
          error: "missing_return_to",
          message: "须通过 L2 网关入口携带 return_to（完整回跳 URL）",
        },
        { status: 400 }
      );
    }
    const ok = validateL2BridgeReturnTo(
      returnTo,
      process.env.L2_BRIDGE_ALLOWED_RETURN_PREFIXES,
    );
    if (!ok) {
      return NextResponse.json(
        {
          error: "invalid_return_to",
          message:
            "回跳地址未在白名单。请在 L1 配置 L2_BRIDGE_ALLOWED_RETURN_PREFIXES（逗号分隔 URL 前缀，须覆盖 L2 公网基址）。",
        },
        { status: 400 }
      );
    }

    const session = await auth();
    const userId = session?.user?.id;
    if (!userId) {
      return NextResponse.json(
        { error: "UNAUTHORIZED", message: "请先登录 Nexus" },
        { status: 401 }
      );
    }

    let organizationId: string | null = null;
    if (isDatabaseConfigured()) {
      const db = getDb()!;
      const rows = await listOrganizationsForUser(db, userId);
      if (!rows.length) {
        return NextResponse.json(
          {
            error: "WORKSPACE_REQUIRED",
            message:
              "尚未加入任何工作区。请先在「工作区」创建或加入组织，再使用 L2 网页桥接登录。",
          },
          { status: 403 }
        );
      }
      const sid = session.user.orgId?.trim();
      const inSession = sid && rows.some((r) => r.orgId === sid);
      if (inSession && sid) {
        organizationId = sid;
      } else {
        const pick = pickSessionDefaultOrg(rows);
        organizationId = pick?.orgId ?? null;
      }
      if (!organizationId) {
        return NextResponse.json(
          {
            error: "NO_ORG_CONTEXT",
            message: "无法确定当前工作区上下文",
          },
          { status: 400 }
        );
      }
      const canGateway = await userCanManageL2Gateway(
        db,
        userId,
        organizationId
      );
      if (!canGateway) {
        return NextResponse.json(
          {
            error: "GATEWAY_FORBIDDEN",
            message:
              "权限不足：仅工作区所有者或管理员可使用网页桥接登录 L2 网关。",
          },
          { status: 403 }
        );
      }
    }

    const code = generateBridgeCode();
    const expiresAt = new Date(Date.now() + TTL_SEC * 1000).toISOString();
    l2BridgeStoreSet({
      code,
      user_id: userId,
      organization_id: organizationId,
      email: session.user.email ?? null,
      expires_at: expiresAt,
    });

    return NextResponse.json({
      bridge_code: code,
      expires_in: TTL_SEC,
    });
  } catch (e) {
    logError("[l2-bridge/mint] Error:", e);
    return NextResponse.json(
      { error: "mint_failed", message: "生成绑定码失败" },
      { status: 500 }
    );
  }
}
