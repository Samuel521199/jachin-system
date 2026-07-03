export type VoiceTier = "CHIT_CHAT" | "SHORT_TASK" | "LONG_TASK";

export type VoiceIntentClass =
  | "CHITCHAT"
  | "QUERY_LIGHT"
  | "TASK_SYNC"
  | "TASK_ASYNC"
  | "CONTROL"
  | "CLARIFY_REPLY"
  | "AMBIGUOUS";

export type VoiceInterruptVerdict =
  | "NONE"
  | "STATUS"
  | "ABORT"
  | "MODIFY"
  | "PARALLEL"
  | "NEW_TASK"
  | "RESUME";

export type VoiceExecutionLane =
  | "direct_llm"
  | "foreground"
  | "background_submit"
  | "background_control"
  | "control_local";

export type VoiceRouteSource = "rule" | "lite_model" | "l3_gateway";

export interface VoiceTaskRef {
  id: string;
  title?: string;
}

export interface VoiceDispatcherContext {
  activeTasks: VoiceTaskRef[];
  awaitingConfirmation?: boolean;
  clarificationPending?: boolean;
  lastFocusTaskId?: string | null;
}

export interface VoiceDispatcherDecision {
  schema_version: 1;
  decision_id: string;
  tier: VoiceTier;
  intent_class: VoiceIntentClass;
  confidence: number;
  interrupt_verdict: VoiceInterruptVerdict;
  task_title?: string;
  target_task_id: string | null;
  execution_lane: VoiceExecutionLane;
  route_source: VoiceRouteSource;
  active_task_ids: string[];
  /** STT 容错后的路由文本；语音进入 L3 时优先发送它，原始 STT 仍放入 implicit_signals。 */
  normalized_text: string;
  route_notes: string[];
  latency_masking: {
    play_task_ack: boolean;
    orb_mode: "idle" | "listening" | "thinking" | "speaking" | "working" | "clarifying";
    hud_terminal: boolean;
  };
  router_hints: {
    force_background: boolean;
    prefer_direct_llm: boolean;
    acceptance_round: boolean;
    max_foreground_tool_sec: number;
    inject_task_context: boolean;
    inject_light_task_context: boolean;
    fast_lane: boolean;
    skip_context_sniffer: boolean;
    skip_experience_rag: boolean;
    skip_gateway_enrich: boolean;
    skip_context_retrieval: boolean;
    clarification_pending: boolean;
    awaiting_confirmation: boolean;
  };
}

const CONTROL_ABORT_RE = /(停|停止|取消|别弄了|算了|不要了|暂停)/;
const CONTROL_STATUS_RE = /(进度|好了吗|怎么样了|做到哪了|状态)/;
const CONTROL_RESUME_RE = /(继续|接着做|恢复)/;
const CONTROL_MODIFY_RE = /(改成|改为|改一下|换成|加上|再加|其实还要)/;

const LONG_TASK_RE = /(全部|批量|所有|每个|整文件夹|整个文件夹|目录|文件夹|生成报告|形成报告|输出报告|汇总报告|分析这些|导出|爬取|所有文件|所有文档)/;
const SHORT_QUERY_RE = /(天气|气温|几点|时间|提醒|闹钟|打开|搜索|读文件|总结这一份|查一下)/;
const CHITCHAT_RE = /(你好|在吗|讲个笑话|陪我聊|心情怎么样|今天过得怎么样)/;

const AMBIGUOUS_RE = /^(帮我处理一下|处理一下|帮我搞一下|看一下|弄一下)$/;

function makeDecisionId(): string {
  return `dec-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function compactAsciiLetters(text: string): string {
  let out = text;
  const spacedWords = ["docs", "docx", "whitepaper", "markdown", "md", "pdf", "txt", "csv", "json", "ppt", "pptx"];
  for (const word of spacedWords) {
    const pattern = word.split("").map((ch) => ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+");
    out = out.replace(new RegExp(`(?<![a-zA-Z])${pattern}(?![a-zA-Z])`, "gi"), word);
  }
  return out;
}

function normalizeVoiceTextForRouting(rawText: string): { text: string; notes: string[] } {
  let text = compactAsciiLetters(rawText.trim());
  const notes: string[] = [];
  const replace = (pattern: RegExp, value: string, note: string) => {
    if (pattern.test(text)) {
      text = text.replace(pattern, value);
      notes.push(note);
    }
  };

  replace(/\bwhite\s*pa(?:per)?\b/gi, "whitepaper", "whitepa->whitepaper");
  replace(/白皮书/g, "whitepaper", "白皮书->whitepaper");
  replace(/\bm\s*d\b/gi, "md", "m d->md");
  replace(/markdown文档|markdown 文件/gi, "md 文档", "markdown->md");
  replace(/d\s*盘|D\s*盘/gi, "D:\\", "D盘->D:\\");
  replace(/project\s*文件夹|project目录|project/g, "D:\\project", "project->D:\\project");
  replace(/charging\s*system|jia\s*chin\s*system|ja\s*chin\s*system|jachin\s*system|贾欣\s*system|佳欣\s*system/gi, "jachin-system-main", "system别名->jachin-system-main");
  replace(/\bwhitepaper\s*文件夹\b/gi, "whitepaper", "whitepaper文件夹->whitepaper");
  replace(/d:\\\s*project/gi, "D:\\project", "修正D盘project路径");
  replace(/D:\\project\s+jachin-system-main/gi, "D:\\project\\jachin-system-main", "补路径分隔符");
  replace(/jachin-system-main\s+docs/gi, "jachin-system-main\\docs", "补docs分隔符");
  replace(/docs\s+whitepaper/gi, "docs\\whitepaper", "补whitepaper分隔符");

  text = text
    .replace(/里面的/g, "下的")
    .replace(/所有\s*md/g, "所有 .md")
    .replace(/全部\s*md/g, "全部 .md")
    .replace(/m d/gi, "md")
    .replace(/\s+/g, " ")
    .trim();

  const hasWhitepaperMdSummary = /whitepaper/i.test(text) && /\.md|md 文档|md文件|md/.test(text) && /(摘要|总结)/.test(text);
  const hasReportRequest = /(生成报告|形成报告|汇总报告|报告)/.test(text);
  if (hasWhitepaperMdSummary && hasReportRequest) {
    const stablePath = "D:\\project\\jachin-system-main\\docs\\whitepaper";
    text = `请把 ${stablePath} 下的所有 .md 文档逐个摘要，并生成一份结构化汇总报告。原始语音识别文本：${rawText.trim()}`;
    notes.push("whitepaper-md-summary-report-template");
  }

  return { text, notes };
}


function containsAny(text: string, words: string[]): boolean {
  return words.some((word) => text.includes(word));
}

function isCompanionFastLaneText(text: string): boolean {
  const t = text.trim();
  if (!t) return false;
  if (t.length > 80) return false;
  if (/[\\/]|\.\w{1,6}\b|```|#|@|http/i.test(t)) return false;
  const heavyWords = [
    "\u6587\u4ef6", "\u76ee\u5f55", "\u9879\u76ee", "\u4ee3\u7801", "\u811a\u672c", "\u62a5\u544a",
    "\u603b\u7ed3", "\u6458\u8981", "\u751f\u6210", "\u4fee\u6539", "\u5220\u9664", "\u8fd0\u884c",
    "\u6267\u884c", "\u641c\u7d22", "\u67e5\u627e", "\u5206\u6790", "\u8868\u683c", "\u6570\u636e\u5e93",
    "\u98de\u4e66", "\u540e\u53f0", "\u4efb\u52a1", "\u6587\u6863", "\u811a\u624b\u67b6",
  ];
  if (containsAny(t, heavyWords)) return false;
  const lightWords = [
    "\u4f60\u597d", "\u5728\u5417", "\u4f60\u5728\u5417", "\u65e9\u4e0a\u597d", "\u4e2d\u5348\u597d", "\u665a\u4e0a\u597d",
    "\u966a\u6211", "\u804a\u804a", "\u4f60\u662f\u8c01", "\u542c\u5f97\u5230\u5417", "\u542c\u89c1\u5417",
    "\u51e0\u70b9", "\u65f6\u95f4", "\u8c22\u8c22", "\u6ca1\u4e8b", "\u7b97\u4e86", "\u597d\u7684",
    "\u5fc3\u60c5", "\u96be\u53d7", "\u5f00\u5fc3", "\u7d2f\u4e86", "\u56f0\u4e86",
    "\u8bb2\u8bdd\u8bb2\u8bdd", "\u8bf4\u8bdd\u8bf4\u8bdd", "\u8bf4\u70b9\u8bdd", "\u8ddf\u6211\u8bf4",
  ];
  if (containsAny(t, lightWords)) return true;
  if (/^(\u597d|\u55ef|\u54e6|\u884c|\u53ef\u4ee5|\u4e0d\u7528|\u6ca1\u4e8b|\u8c22\u8c22|\u8f9b\u82e6\u4e86)[\u3002\uff01\uff1f!?]?$/.test(t)) return true;
  return false;
}
function isSafeDefaultChitChatFastLaneText(text: string, hasActiveTask: boolean): boolean {
  const t = text.trim();
  if (!t || hasActiveTask) return false;
  if (t.length > 60) return false;
  if (/[\\/]|\.\w{1,6}\b|```|#|@|http/i.test(t)) return false;
  const taskWords = [
    "\u6587\u4ef6", "\u76ee\u5f55", "\u9879\u76ee", "\u4ee3\u7801", "\u811a\u672c", "\u62a5\u544a",
    "\u603b\u7ed3", "\u6458\u8981", "\u751f\u6210", "\u4fee\u6539", "\u5220\u9664", "\u8fd0\u884c",
    "\u6267\u884c", "\u641c\u7d22", "\u67e5\u627e", "\u5206\u6790", "\u8868\u683c", "\u6570\u636e\u5e93",
    "\u98de\u4e66", "\u540e\u53f0", "\u4efb\u52a1", "\u6587\u6863", "\u811a\u624b\u67b6",
    "\u6253\u5f00", "\u5173\u95ed", "\u5199", "\u4f5c\u6587", "\u5e2e\u6211", "\u7ed9\u6211", "\u8bf7\u4f60",
  ];
  return !containsAny(t, taskWords);
}
function markFastLane(decision: VoiceDispatcherDecision): void {
  decision.tier = "CHIT_CHAT";
  decision.intent_class = "CHITCHAT";
  decision.execution_lane = "direct_llm";
  decision.confidence = Math.max(decision.confidence, 0.9);
  decision.router_hints.prefer_direct_llm = true;
  decision.router_hints.inject_task_context = false;
  decision.router_hints.inject_light_task_context = false;
  decision.router_hints.fast_lane = true;
  decision.router_hints.skip_context_retrieval = true;
  decision.router_hints.skip_context_sniffer = true;
  decision.router_hints.skip_experience_rag = true;
  decision.router_hints.skip_gateway_enrich = true;
}
function matchTaskIdFromText(text: string, ctx: VoiceDispatcherContext): string | null {
  const lowered = text.toLowerCase();
  for (const task of ctx.activeTasks) {
    const t = (task.title || "").trim();
    if (t && lowered.includes(t.toLowerCase())) return task.id;
  }
  if (ctx.activeTasks.length === 1) return ctx.activeTasks[0].id;
  if (ctx.lastFocusTaskId && ctx.activeTasks.some((t) => t.id === ctx.lastFocusTaskId)) return ctx.lastFocusTaskId;
  return null;
}

function isBulkDocumentReportTask(text: string): boolean {
  const hasBulk = /(全部|批量|所有|每个|整文件夹|整个文件夹|所有文件|所有文档)/.test(text);
  const hasDocs = /(\.md|md 文档|md文件|markdown|文档|文件)/i.test(text);
  const hasOutput = /(摘要|总结|生成报告|形成报告|汇总报告|报告)/.test(text);
  return hasBulk && hasDocs && hasOutput;
}


function wordScore(text: string, words: string[]): number {
  return words.reduce((score, word) => score + (text.includes(word) ? 1 : 0), 0);
}

function isLikelyLongVoiceTask(text: string): boolean {
  const scopeWords = ["\u5168\u90e8", "\u6240\u6709", "\u6bcf\u4e2a", "\u6574\u4e2a", "\u6574\u6279", "\u6279\u91cf", "\u9010\u4e2a", "\u5168\u76d8"];
  const objectWords = [
    "\u6587\u4ef6", "\u6587\u6863", "\u76ee\u5f55", "\u6587\u4ef6\u5939", "markdown", "md", "\u8868\u683c", "\u6570\u636e",
    "\u5783\u573e\u6587\u4ef6", "\u78c1\u76d8", "\u786c\u76d8",
  ];
  const actionWords = [
    "\u751f\u6210", "\u8f93\u51fa", "\u5f62\u6210", "\u6c47\u603b", "\u603b\u7ed3", "\u6458\u8981", "\u5206\u6790", "\u5bfc\u51fa", "\u62a5\u544a",
    "\u626b\u63cf", "\u6e05\u7406", "\u67e5\u627e", "\u67e5\u6740",
  ];
  const hasDiskScope = /[a-zA-Z]\s*(?:[:：]\s*)?\u76d8/.test(text);
  return (wordScore(text, scopeWords) >= 1 || hasDiskScope) && wordScore(text, objectWords) >= 1 && wordScore(text, actionWords) >= 1;
}

function hasAbortFollowupIntent(text: string): boolean {
  if (!CONTROL_ABORT_RE.test(text)) return false;
  return /(先|然后|接着|再|顺便|讲个|讲一个|说个|说一个|给我|帮我)/.test(text);
}

function hasResumeChatFollowupIntent(text: string): boolean {
  if (!CONTROL_RESUME_RE.test(text)) return false;
  return /(陪我|聊聊|说两句|讲个|讲一个|笑话|焦虑|难受|开心|累了|困了|心情)/.test(text);
}

function isLikelyShortVoiceTask(text: string): boolean {
  if (text.length > 80) return false;
  const shortWords = ["\u51e0\u70b9", "\u65f6\u95f4", "\u5929\u6c14", "\u6c14\u6e29", "\u6253\u5f00", "\u63d0\u9192", "\u95f9\u949f", "\u67e5\u4e00\u4e0b", "\u770b\u4e00\u4e0b"];
  const heavyWords = ["\u6240\u6709", "\u6279\u91cf", "\u751f\u6210\u62a5\u544a", "\u6574\u4e2a\u9879\u76ee", "\u6539\u4ee3\u7801"];
  return wordScore(text, shortWords) >= 1 && wordScore(text, heavyWords) === 0;
}
export function dispatchVoiceIntent(rawText: string, ctx: VoiceDispatcherContext): VoiceDispatcherDecision {
  const raw = rawText.trim();
  const normalized = normalizeVoiceTextForRouting(raw);
  const text = normalized.text;
  const activeTaskIds = ctx.activeTasks.map((t) => t.id);
  const decision: VoiceDispatcherDecision = {
    schema_version: 1,
    decision_id: makeDecisionId(),
    tier: "CHIT_CHAT",
    intent_class: "CHITCHAT",
    confidence: 0.72,
    interrupt_verdict: "NONE",
    target_task_id: null,
    execution_lane: "direct_llm",
    route_source: "rule",
    active_task_ids: activeTaskIds,
    normalized_text: text,
    route_notes: normalized.notes,
    latency_masking: {
      play_task_ack: false,
      orb_mode: "listening",
      hud_terminal: false,
    },
    router_hints: {
      force_background: false,
      prefer_direct_llm: true,
      acceptance_round: false,
      max_foreground_tool_sec: 5,
      inject_task_context: activeTaskIds.length > 0,
      inject_light_task_context: false,
      fast_lane: false,
      skip_context_sniffer: false,
      skip_experience_rag: false,
      skip_gateway_enrich: false,
      skip_context_retrieval: false,
      clarification_pending: Boolean(ctx.clarificationPending),
      awaiting_confirmation: Boolean(ctx.awaitingConfirmation),
    },
  };

  if (!raw) return decision;

  const hasActiveTask = activeTaskIds.length > 0;

  if (ctx.awaitingConfirmation && /^(对|好的|嗯|是的|就这样|一起跑|同时|等完)/.test(raw)) {
    decision.intent_class = "CLARIFY_REPLY";
    decision.confidence = 0.85;
    return decision;
  }

  if (ctx.clarificationPending && /^(对|好的|嗯|是的|就这样|文件|邮件|这个|那个)/.test(raw)) {
    decision.intent_class = "CLARIFY_REPLY";
    decision.confidence = 0.82;
    return decision;
  }

  if (hasActiveTask) {
    if (CONTROL_ABORT_RE.test(text)) {
      decision.intent_class = "CONTROL";
      decision.interrupt_verdict = "ABORT";
      decision.target_task_id = matchTaskIdFromText(text, ctx);
      decision.execution_lane = "background_control";
      decision.confidence = decision.target_task_id ? 0.92 : 0.55;
      decision.router_hints.prefer_direct_llm = false;
      if (hasAbortFollowupIntent(text)) decision.route_notes.push("abort+new_task");
      return decision;
    }
    if (CONTROL_STATUS_RE.test(text)) {
      decision.intent_class = "CONTROL";
      decision.interrupt_verdict = "STATUS";
      decision.target_task_id = matchTaskIdFromText(text, ctx);
      decision.execution_lane = "background_control";
      decision.confidence = decision.target_task_id ? 0.9 : 0.6;
      decision.router_hints.prefer_direct_llm = false;
      return decision;
    }
    if (CONTROL_MODIFY_RE.test(text)) {
      decision.intent_class = "CONTROL";
      decision.interrupt_verdict = "MODIFY";
      decision.target_task_id = matchTaskIdFromText(text, ctx);
      decision.execution_lane = "background_control";
      decision.confidence = decision.target_task_id ? 0.88 : 0.6;
      decision.router_hints.prefer_direct_llm = false;
      return decision;
    }
    if (CONTROL_RESUME_RE.test(text) && !hasResumeChatFollowupIntent(text)) {
      decision.intent_class = "CONTROL";
      decision.interrupt_verdict = "RESUME";
      decision.target_task_id = matchTaskIdFromText(text, ctx);
      decision.execution_lane = "background_control";
      decision.confidence = decision.target_task_id ? 0.88 : 0.55;
      decision.router_hints.prefer_direct_llm = false;
      return decision;
    }
  }

  if (AMBIGUOUS_RE.test(text)) {
    decision.intent_class = "AMBIGUOUS";
    decision.confidence = 0.35;
    decision.router_hints.prefer_direct_llm = false;
    decision.latency_masking.orb_mode = "clarifying";
    return decision;
  }

  if (isBulkDocumentReportTask(text) || isLikelyLongVoiceTask(text) || LONG_TASK_RE.test(text)) {
    decision.tier = "LONG_TASK";
    decision.intent_class = "TASK_ASYNC";
    decision.execution_lane = "background_submit";
    decision.confidence = isBulkDocumentReportTask(text) || isLikelyLongVoiceTask(text) ? 0.94 : 0.9;
    decision.task_title = /whitepaper/i.test(text) ? "whitepaper md 摘要报告" : "语音长任务";
    decision.latency_masking.play_task_ack = true;
    decision.latency_masking.orb_mode = "working";
    decision.latency_masking.hud_terminal = true;
    decision.router_hints.force_background = true;
    decision.router_hints.acceptance_round = true;
    decision.router_hints.prefer_direct_llm = false;
    return decision;
  }

  if (isLikelyShortVoiceTask(text) || SHORT_QUERY_RE.test(text)) {
    decision.tier = "SHORT_TASK";
    decision.intent_class = "TASK_SYNC";
    decision.execution_lane = "foreground";
    decision.confidence = 0.84;
    decision.latency_masking.play_task_ack = true;
    decision.latency_masking.orb_mode = "thinking";
    decision.router_hints.prefer_direct_llm = false;
    return decision;
  }

  if (CHITCHAT_RE.test(text)) {
    markFastLane(decision);
    decision.confidence = 0.9;
    if (hasActiveTask) {
      decision.router_hints.inject_light_task_context = true;
      decision.route_notes.push("chitchat-fast-lane-active-task");
    } else {
      decision.route_notes.push("chitchat-fast-lane");
    }
    return decision;
  }

  if (isCompanionFastLaneText(text)) {
    markFastLane(decision);
    if (hasActiveTask) {
      decision.router_hints.inject_light_task_context = true;
      decision.route_notes.push("companion-fast-lane-active-task");
    } else {
      decision.route_notes.push("companion-fast-lane");
    }
    return decision;
  }

  if (isSafeDefaultChitChatFastLaneText(text, hasActiveTask)) {
    markFastLane(decision);
    decision.confidence = 0.86;
    decision.route_notes.push("default-chitchat-fast-lane");
    return decision;
  }
  if (hasActiveTask) {
    decision.intent_class = "CONTROL";
    decision.interrupt_verdict = "PARALLEL";
    decision.execution_lane = "direct_llm";
    decision.confidence = 0.68;
  }

  return decision;
}
