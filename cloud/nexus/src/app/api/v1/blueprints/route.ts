import { NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { blueprints } from "@/db/schema";
import { desc } from "drizzle-orm";

/**
 * GET /api/v1/blueprints
 * 蓝图武库 - 拉取已铸造的蓝图列表
 */
export async function GET() {
  try {
    if (!isDatabaseConfigured()) {
      return NextResponse.json({ blueprints: [] });
    }

    const db = getDb()!;
    const data = await db
      .select({ id: blueprints.id, name: blueprints.name, description: blueprints.description })
      .from(blueprints)
      .orderBy(desc(blueprints.createdAt))
      .limit(50);

    return NextResponse.json({
      blueprints: data.map((b) => ({
        id: b.id,
        name: b.name,
        description: b.description ?? "",
      })),
    });
  } catch (e) {
    console.error("[blueprints] Error:", e);
    return NextResponse.json(
      { error: "获取蓝图列表失败" },
      { status: 500 }
    );
  }
}
