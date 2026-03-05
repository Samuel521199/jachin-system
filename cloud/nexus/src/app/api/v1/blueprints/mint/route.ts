import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { blueprints } from "@/db/schema";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

/**
 * POST /api/v1/blueprints/mint
 * Forge 蓝图铸造 - 将 React Flow AST 写入 blueprints 表
 * Body: { name, ast_json, description?, price? }
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const { name, ast_json, description, price } = body;

    if (!name || typeof name !== "string") {
      return NextResponse.json(
        { success: false, error: "Missing or invalid name" },
        { status: 400 }
      );
    }

    if (!ast_json || typeof ast_json !== "object") {
      return NextResponse.json(
        { success: false, error: "Missing or invalid ast_json" },
        { status: 400 }
      );
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        { success: false, error: "数据库未配置，无法写入" },
        { status: 503 }
      );
    }

    const db = getDb()!;
    const creatorId = body.creator_id ?? DEFAULT_USER_ID;

    const [blueprint] = await db
      .insert(blueprints)
      .values({
        creatorId,
        name: String(name).trim(),
        description: description ? String(description).trim() : null,
        astJson: ast_json,
        price: typeof price === "number" ? String(price) : "0",
      })
      .returning({ id: blueprints.id, name: blueprints.name, createdAt: blueprints.createdAt });

    if (!blueprint) {
      return NextResponse.json(
        { success: false, error: "插入失败" },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      message: "AST 语法树已成功写入底层数据库！版税资产已确权！",
      blueprint: {
        id: blueprint.id,
        name: blueprint.name,
        created_at: blueprint.createdAt,
      },
    });
  } catch (e) {
    console.error("[blueprints/mint] Error:", e);
    return NextResponse.json(
      { success: false, error: (e as Error).message },
      { status: 500 }
    );
  }
}
