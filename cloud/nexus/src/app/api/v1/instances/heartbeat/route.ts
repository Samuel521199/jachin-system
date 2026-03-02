import { NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

/**
 * POST /api/v1/instances/heartbeat
 * P0-4 端云心跳 - 前线边缘智能体向指挥部汇报生命体征
 *
 * Body: { instance_id, core_version, metrics, active_plugins }
 * Headers: Authorization: Bearer <access_token>
 */
export async function POST(req: Request) {
  try {
    const authHeader = req.headers.get("Authorization");
    if (!authHeader?.startsWith("Bearer ")) {
      return NextResponse.json(
        { error: "防线拦截：未出示边缘智能体通行证" },
        { status: 401 }
      );
    }
    const token = authHeader.slice(7).trim();

    const body = await req.json();
    const { instance_id, core_version, metrics, active_plugins } = body;

    if (!instance_id) {
      return NextResponse.json(
        { error: "Missing instance_id" },
        { status: 400 }
      );
    }

    // 鉴权：校验 Token 是否属于该边缘智能体（pairing_sessions + layer2_instances）
    if (isSupabaseConfigured()) {
      const sb = getSupabase()!;
      const { data: session, error: authError } = await sb
        .from("pairing_sessions")
        .select("session_id, status, layer2_instance_id")
        .eq("session_id", token)
        .maybeSingle();

      if (authError) {
        console.error("Heartbeat auth query error:", authError);
        return NextResponse.json(
          { error: "防线拦截：身份校验异常" },
          { status: 500 }
        );
      }

      if (!session || session.status !== "approved") {
        console.warn(
          `🚨 警告：截获到伪造的心跳包！来源边缘智能体 ID: ${instance_id}`
        );
        return NextResponse.json(
          {
            error:
              "防线拦截：边缘智能体身份验证失败，拒绝接入大盘！",
          },
          { status: 403 }
        );
      }

      // 若已绑定 layer2_instance，校验 instance_id 一致性
      if (session.layer2_instance_id) {
        const { data: linkedInstance } = await sb
          .from("layer2_instances")
          .select("instance_id")
          .eq("id", session.layer2_instance_id)
          .maybeSingle();

        if (
          linkedInstance &&
          linkedInstance.instance_id &&
          linkedInstance.instance_id !== instance_id
        ) {
          console.warn(
            `🚨 警告：Token 与边缘智能体 ID 不匹配！期望: ${linkedInstance.instance_id}, 收到: ${instance_id}`
          );
          return NextResponse.json(
            {
              error:
                "防线拦截：边缘智能体身份验证失败，拒绝接入大盘！",
            },
            { status: 403 }
          );
        }
      }
    }

    const updatePayload = {
      core_version: core_version ?? null,
      active_plugins: active_plugins ?? {},
      metrics: metrics ?? {},
      last_heartbeat: new Date().toISOString(),
    };

    if (isSupabaseConfigured()) {
      const sb = getSupabase()!;
      const { error } = await sb
        .from("layer2_instances")
        .upsert(
          {
            instance_id,
            ...updatePayload,
            environment_type: "bare_metal",
          },
          { onConflict: "instance_id" }
        );

      if (error) {
        console.error("Heartbeat DB error:", error);
        return NextResponse.json(
          { error: "心跳写入失败" },
          { status: 500 }
        );
      }
    }

    console.log(`\n🛸 收到边缘智能体 [${String(instance_id).slice(0, 8)}] 心跳:`);
    console.log(
      `   💻 CPU: ${metrics?.cpu_percent ?? "?"}% | RAM: ${metrics?.ram_used_mb ?? "?"}MB`
    );
    console.log(`   📦 武器状态:`, active_plugins ?? {});

    return NextResponse.json({ success: true, timestamp: Date.now() });
  } catch (e) {
    console.error("Heartbeat Error:", e);
    return NextResponse.json(
      { error: "心跳处理失败" },
      { status: 500 }
    );
  }
}
