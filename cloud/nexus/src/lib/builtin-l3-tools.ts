/**
 * L3 运行时内置 Native 工具（core:/util:/sys:）— 非独立下载包，仅在商城 TOOL 分类展示说明。
 * 与 plugins_registry 中的「可订阅 TOOL 包」合并展示；内置项带 runtime_builtin，禁止走订阅。
 */
import { createHash } from "node:crypto";

export type BuiltinL3ToolEntry = {
  tool_id: string;
  name: string;
  description: string;
};

/** 与 loader / core_util_tools 对齐；描述摘自源码 desc（便于商城展示） */
export const BUILTIN_L3_TOOLS: BuiltinL3ToolEntry[] = [
  { tool_id: "core:fs_read", name: "core:fs_read", description: "读取文件内容（白名单路径）。" },
  { tool_id: "core:fs_write", name: "core:fs_write", description: "写入文件（workspace / 桌面等白名单）。" },
  { tool_id: "core:shell_exec", name: "core:shell_exec", description: "在 workspace 执行 Shell，可后台。" },
  { tool_id: "core:shell_job_status", name: "core:shell_job_status", description: "查询后台 shell 任务。" },
  { tool_id: "core:shell_job_cancel", name: "core:shell_job_cancel", description: "取消后台 shell。" },
  { tool_id: "core:apply_patch", name: "core:apply_patch", description: "将 unified diff 应用到 workspace。" },
  { tool_id: "core:apply_patch_rollback", name: "core:apply_patch_rollback", description: "回滚 apply_patch。" },
  { tool_id: "core:submit_background_task", name: "core:submit_background_task", description: "投递长耗时后台任务。" },
  { tool_id: "core:check_background_task", name: "core:check_background_task", description: "查询后台任务状态。" },
  { tool_id: "core:local_memory_search", name: "core:local_memory_search", description: "本地记忆检索（l3_local.json）。" },
  { tool_id: "core:local_memory_append", name: "core:local_memory_append", description: "追加本地记忆条目。" },
  { tool_id: "core:safety_lock_append", name: "core:safety_lock_append", description: "提交安防规则（待审批）。" },
  { tool_id: "core:safety_lock_list_pending", name: "core:safety_lock_list_pending", description: "列出安全锁待审批。" },
  { tool_id: "core:safety_lock_remove", name: "core:safety_lock_remove", description: "删除安全锁条目。" },
  { tool_id: "core:workflow_run", name: "core:workflow_run", description: "执行 workspace YAML 工作流。" },
  { tool_id: "core:domain_workflow_run", name: "core:domain_workflow_run", description: "执行领域子图。" },
  { tool_id: "core:shell_hitl_approve", name: "core:shell_hitl_approve", description: "批准 Shell 人机确认。" },
  { tool_id: "core:compose_essay", name: "core:compose_essay", description: "生成 Markdown 作文骨架。" },
  { tool_id: "util:datetime_calc", name: "util:datetime_calc", description: "日期时间计算（时区、加天数）。" },
  { tool_id: "util:cron_explain", name: "util:cron_explain", description: "解析 Cron 表达式。" },
  { tool_id: "util:precise_math", name: "util:precise_math", description: "安全四则运算（Decimal）。" },
  { tool_id: "util:uuid_gen", name: "util:uuid_gen", description: "生成 UUID v4。" },
  { tool_id: "util:hash_crypto", name: "util:hash_crypto", description: "哈希 / Base64。" },
  { tool_id: "util:json_jq", name: "util:json_jq", description: "JSON 按路径取值。" },
  { tool_id: "util:regex_test", name: "util:regex_test", description: "正则测试。" },
  { tool_id: "util:http_ping", name: "util:http_ping", description: "HTTP HEAD/GET 探测。" },
  { tool_id: "util:stealth_extract", name: "util:stealth_extract", description: "智能抓取（轻装 + 可选旁路）。" },
  { tool_id: "util:dns_lookup", name: "util:dns_lookup", description: "DNS 解析。" },
  { tool_id: "util:get_weather_lite", name: "util:get_weather_lite", description: "极简天气（wttr / Open-Meteo）。" },
  { tool_id: "util:ab_test_calc", name: "util:ab_test_calc", description: "A/B Z 检验。" },
  { tool_id: "util:fake_data_gen", name: "util:fake_data_gen", description: "Faker 占位数据。" },
  { tool_id: "util:text_diff", name: "util:text_diff", description: "文本 unified diff。" },
  { tool_id: "util:funnel_calc", name: "util:funnel_calc", description: "漏斗 / ROI。" },
  { tool_id: "util:generate_office_doc", name: "util:generate_office_doc", description: "原生 Word/Excel 生成。" },
  { tool_id: "util:compose_long_document", name: "util:compose_long_document", description: "万字级长文分章撰写拼装。" },
  { tool_id: "util:desktop_message_box", name: "util:desktop_message_box", description: "本机立即弹窗提醒。" },
  { tool_id: "util:schedule_desktop_reminder", name: "util:schedule_desktop_reminder", description: "桌面定时哨兵提醒（HTTP 8002）。" },
  { tool_id: "util:lark_send_text", name: "util:lark_send_text", description: "飞书/Lark 发纯文本（需应用凭证）。" },
  { tool_id: "sys:health_stats", name: "sys:health_stats", description: "CPU/内存/磁盘。" },
  { tool_id: "sys:list_env_safe", name: "sys:list_env_safe", description: "列出环境变量名（无值）。" },
];

/** 稳定 UUID（与订阅校验格式一致），同一 tool_id 永远相同 */
export function stableCatalogIdForBuiltinTool(toolId: string): string {
  const h = createHash("sha256").update(`jachin:nexus:builtin_tool:${toolId}`).digest("hex");
  return [
    h.slice(0, 8),
    h.slice(8, 12),
    "5" + h.slice(12, 15),
    ((parseInt(h.slice(16, 18), 16) & 0x3f) | 0x80).toString(16).padStart(2, "0") + h.slice(18, 20),
    h.slice(20, 32),
  ].join("-");
}

const _BUILTIN_IDS = new Set(BUILTIN_L3_TOOLS.map((t) => stableCatalogIdForBuiltinTool(t.tool_id)));

export function isBuiltinToolCatalogId(id: string): boolean {
  return _BUILTIN_IDS.has(id);
}

export type CatalogRow = {
  id: string;
  plugin_id: string | null;
  item_type: "TOOL";
  name: string;
  description: string | null;
  developer_id: string | null;
  price_monthly: number;
  runtime_tier: string;
  required_mcps: string[];
  package_url: string | null;
  created_at: string | null;
  runtime_builtin: boolean;
  tool_id: string;
};

export function builtinToolsToCatalogRows(): CatalogRow[] {
  return BUILTIN_L3_TOOLS.map((e) => ({
    id: stableCatalogIdForBuiltinTool(e.tool_id),
    plugin_id: null,
    item_type: "TOOL" as const,
    name: e.name,
    description: e.description,
    developer_id: "Jachin L3",
    price_monthly: 0,
    runtime_tier: "L3_LOCAL",
    required_mcps: [],
    package_url: null,
    created_at: null,
    runtime_builtin: true,
    tool_id: e.tool_id,
  }));
}
