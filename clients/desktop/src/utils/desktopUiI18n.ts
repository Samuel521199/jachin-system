import type { DesktopUiLang } from "./desktopUiLang";

/** Horizon 顶栏 */
export const desktopHorizon = {
  zh: {
    gpuHot: "⚠ 算力过热",
    gpuHotTitle: "GPU 过热，建议分流任务到云端",
    exit: "退出",
    exitTitle:
      "完全退出 Jachin（结束进程；关闭主窗口仅会隐藏，请用此处或托盘菜单退出）",
  },
  en: {
    gpuHot: "⚠ GPU hot",
    gpuHotTitle: "GPU overheating — consider offloading to cloud",
    exit: "Exit",
    exitTitle: "Quit Jachin completely. Closing the main window only hides it; use here or the tray menu to exit.",
  },
} as const;

/** Omni 对话壳层 + 输入占位（chat.tsx 与 shell 共用） */
export const desktopOmniUi = {
  zh: {
    closeSessionList: "关闭会话列表",
    newChatSidebar: "发起新对话",
    newChatTitle: "新建对话",
    newChatFallback: "新对话",
    deleteSession: "删除会话",
    sessionHistory: "会话历史",
    emptyThreadHint: "在此输入，对话将按轮次显示",
    hitlTitle: "HITL · 需人工授权",
    hitlFallback: "[高危操作待确认]",
    hitlApprove: "授权通过",
    hitlReject: "拦截销毁",
    vadListening: "VAD 监听中…",
    stopGeneration: "停止生成",
    largeConsole: "大控制台",
    settingsConsole: "设置（控制台）",
    placeholderL3: "Alt+Shift+Space · 输入…",
    placeholderL2: "Alt+Shift+Space · 输入指令（L2）…",
    placeholderWait: "等待 L3 或 L2…",
    reasoningChain: "思考链",
    reasoningExpand: "（可展开）",
    reasoningUpdating: "更新中",
  },
  en: {
    closeSessionList: "Close session list",
    newChatSidebar: "New chat",
    newChatTitle: "New chat",
    newChatFallback: "New chat",
    deleteSession: "Delete session",
    sessionHistory: "Session history",
    emptyThreadHint: "Type here — messages appear in order",
    hitlTitle: "HITL · Approval required",
    hitlFallback: "[High-risk action pending]",
    hitlApprove: "Approve",
    hitlReject: "Reject",
    vadListening: "VAD listening…",
    stopGeneration: "Stop generation",
    largeConsole: "Console",
    settingsConsole: "Settings (console)",
    placeholderL3: "Alt+Shift+Space · type…",
    placeholderL2: "Alt+Shift+Space · type a command (L2)…",
    placeholderWait: "Waiting for L3 or L2…",
    reasoningChain: "Chain of thought",
    reasoningExpand: "(expand)",
    reasoningUpdating: "Updating",
  },
} as const;

export type DesktopOmniUiStrings = (typeof desktopOmniUi)[DesktopUiLang];

export function getDesktopOmniUi(lang: DesktopUiLang): DesktopOmniUiStrings {
  return desktopOmniUi[lang];
}

/**
 * L3 控制台（侧栏、Dashboard、拓扑等）— 与 Horizon 语言键一致。
 * 品牌/导航英文名（Dashboard、Neural Nexus、Skill Matrix…）在两种语言下保持英文；仅翻译日常功能词。
 */
export const desktopConsole = {
  zh: {
    sidebar: {
      safetyLock: "安全锁审批",
      safetyLockTitle:
        "待审批安全锁条目：写入 JACHIN_SAFETY_LOCK.md（需管理员密钥）",
      calendar: "日历",
      calendarTitle: "事件、提醒、待办，支持循环",
      wakeMode: "唤醒模式",
      wakeModeTitle: "设置唤醒词/名字，启动唤醒监听（模式 B）",
      preferences: "设置",
      preferencesTitle: "AI 模式与运行模式",
    },
    dashboard: {
      quickActionsTitle: "Quick Actions",
      quickPrivacy: "隐私模式",
      quickPrivacyTitle: "点击切换：开启后本地数据不再上报。",
      quickClean: "清理内存",
      quickCleanTitle: "触发内存清理。",
      quickEagle: "鹰眼",
      quickEagleTitle: "切换控制台显示/隐藏。",
      quickSleep: "休眠",
      quickSleepTitle: "切换精灵与聊天窗口显示。",
      quickToggleOn: "已开",
      vadHeading: "VAD 语音采集",
      vadStart: "开始 VAD",
      vadStop: "停止 VAD",
      vadCapturing: "采集中",
      agendaTitle: "Agenda & Suggestions",
      agendaSubtitle: "调度 · 系统 · 记忆 — 可执行建议（无后端时为本地演示）",
      agendaEmpty: "暂无建议条目",
      agendaStat: "共 {n} 条",
    },
    demoSuggestions: [
      {
        id: "1",
        text: "明天上午 9 点有会议，需要整理相关邮件吗？",
        action: "执行",
        type: "calendar",
      },
      {
        id: "2",
        text: "C 盘空间不足 10%，建议清理缓存。",
        action: "清理",
        type: "system",
      },
      {
        id: "3",
        text: "检测到 3 个待办事项未完成，要现在处理吗？",
        action: "查看",
        type: "task",
      },
      {
        id: "4",
        text: "L3 推理策略当前为「默认」，是否切换为「高性能」以缩短响应？",
        action: "sync",
        type: "strategy",
      },
      {
        id: "5",
        text: "检测到长时间未同步技能清单，是否从网关拉取最新库存？",
        action: "later",
        type: "inventory",
      },
      {
        id: "6",
        text: "有一条低优先级提醒：今日步数目标未设置，要忽略此类健康提示吗？",
        action: "dismiss",
        type: "health",
      },
    ] as const,
    suggestionActionLabels: {
      执行: "执行",
      清理: "清理",
      查看: "查看",
      添加: "添加",
      later: "稍后",
      sync: "应用",
      dismiss: "忽略",
    } as Record<string, string>,
    mind: {
      waiting1: "等待连接…",
      waiting2: "请确保后端已启动 (scripts\\start.ps1)",
      statusLive: "Live",
      statusError: "连接异常",
    },
    topology: {
      runMode: "运行模式:",
      collapse: "▼ 收起",
      expandDetails: "▶ 节点与任务详情",
      nodesLabel: "节点:",
      runningLabel: "运行中:",
      strategyEco: "节能",
      strategyDefault: "默认",
      strategyPerformance: "高性能",
      strategyGod: "上帝模式",
    },
    heartbeat: {
      gpuHot: "⚠ GPU 过热 ({temp}°C)",
      gpuHotTitle: "算力负载过高，建议分流任务到云端",
    },
  },
  en: {
    sidebar: {
      safetyLock: "Security lock",
      safetyLockTitle:
        "Pending safety-lock entries → JACHIN_SAFETY_LOCK.md (admin key required)",
      calendar: "Calendar",
      calendarTitle: "Events, reminders, to-dos, recurrence",
      wakeMode: "Wake mode",
      wakeModeTitle: "Wake word / name and wake listener (mode B)",
      preferences: "Settings",
      preferencesTitle: "AI mode and runtime preferences",
    },
    dashboard: {
      quickActionsTitle: "Quick Actions",
      quickPrivacy: "Privacy mode",
      quickPrivacyTitle: "Toggle: when on, local data is not uploaded.",
      quickClean: "Clear memory",
      quickCleanTitle: "Trigger memory cleanup.",
      quickEagle: "Eagle eye",
      quickEagleTitle: "Toggle console visibility.",
      quickSleep: "Sleep",
      quickSleepTitle: "Toggle sprite and chat window visibility.",
      quickToggleOn: "On",
      vadHeading: "VAD capture",
      vadStart: "Start VAD",
      vadStop: "Stop VAD",
      vadCapturing: "Capturing",
      agendaTitle: "Agenda & Suggestions",
      agendaSubtitle: "Schedule · system · memory — actionable cards (local demo if API empty)",
      agendaEmpty: "No suggestions yet",
      agendaStat: "{n} items",
    },
    demoSuggestions: [
      {
        id: "1",
        text: "Meeting at 9:00 tomorrow — triage related emails?",
        action: "执行",
        type: "calendar",
      },
      {
        id: "2",
        text: "Drive C is under 10% free — clear cache?",
        action: "清理",
        type: "system",
      },
      {
        id: "3",
        text: "3 to-dos pending — open now?",
        action: "查看",
        type: "task",
      },
      {
        id: "4",
        text: "L3 inference profile is Default — switch to Performance for lower latency?",
        action: "sync",
        type: "strategy",
      },
      {
        id: "5",
        text: "Skills inventory has not synced for a while — pull latest from gateway now?",
        action: "later",
        type: "inventory",
      },
      {
        id: "6",
        text: "Low-priority nudge: daily step goal not set — dismiss health tips like this?",
        action: "dismiss",
        type: "health",
      },
    ] as const,
    suggestionActionLabels: {
      执行: "Run",
      清理: "Clean",
      查看: "Open",
      添加: "Add",
      later: "Later",
      sync: "Apply",
      dismiss: "Dismiss",
    } as Record<string, string>,
    mind: {
      waiting1: "Waiting for connection…",
      waiting2: "Ensure the backend is running (scripts\\start.ps1)",
      statusLive: "Live",
      statusError: "Connection issue",
    },
    topology: {
      runMode: "Run mode:",
      collapse: "▼ Collapse",
      expandDetails: "▶ Nodes & tasks",
      nodesLabel: "Nodes:",
      runningLabel: "Running:",
      strategyEco: "Eco",
      strategyDefault: "Default",
      strategyPerformance: "Performance",
      strategyGod: "God mode",
    },
    heartbeat: {
      gpuHot: "⚠ GPU hot ({temp}°C)",
      gpuHotTitle: "High load — consider offloading to cloud",
    },
  },
} as const;

export type DesktopConsoleStrings = (typeof desktopConsole)[DesktopUiLang];

export function getDesktopConsole(lang: DesktopUiLang): DesktopConsoleStrings {
  return desktopConsole[lang];
}

/**
 * Mind Stream：后端可能输出中文调试句，英文界面下仅做展示层替换（不改变 Skill/MCP/Jachin 等专名）。
 */
export function localizeMindStreamLine(line: string, lang: DesktopUiLang): string {
  if (lang !== "en" || !line) return line;
  let s = line;
  const pairs: [RegExp, string][] = [
    [
      /\[EventBroadcaster\]\s*客户端已断开，当前订阅者=(\d+)/,
      "[EventBroadcaster] Client disconnected, subscribers=$1",
    ],
    [
      /\[EventBroadcaster\]\s*客户端已连接，当前订阅者=(\d+)/,
      "[EventBroadcaster] Client connected, subscribers=$1",
    ],
    [/\[Layer3\]\s*客户端已断开/, "[Layer3] Client disconnected"],
    [
      /\[Layer3\]\s*客户端已连接 ws:\/\/localhost:(\d+)([^\s]*)/,
      "[Layer3] Client connected ws://localhost:$1$2",
    ],
  ];
  for (const [re, rep] of pairs) {
    if (re.test(s)) {
      s = s.replace(re, rep);
      break;
    }
  }
  return s;
}
