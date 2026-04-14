/**
 * L1 Nexus 全站 UI 文案（轻量 i18n，不引入 i18next）
 * 持久化键见 NexusUiLangProvider
 */

import type { OrgRole } from "@/lib/org-constants";

export type NexusUiLang = "zh" | "en";

export const NEXUS_UI_LANG_STORAGE_KEY = "jachin-nexus-ui-lang";

export function readNexusUiLangFromStorage(): NexusUiLang {
  if (typeof window === "undefined") return "zh";
  const v = window.localStorage.getItem(NEXUS_UI_LANG_STORAGE_KEY);
  return v === "en" ? "en" : "zh";
}

/** 顶栏导航（与落地页 Dock 顺序、路由一致） */
export const nexusNav = {
  zh: {
    store: "应用商店",
    analytics: "审计大屏",
    payouts: "收纳中心",
    plugins: "我的作品",
    legal: "法律市场",
    market: "神经集市",
    forge: "锻造炉",
    plaza: "广场",
    console: "控制台",
    workspace: "工作区",
    fleet: "舰队",
    pair: "添加智能体",
  },
  en: {
    store: "Store",
    analytics: "Dashboard",
    payouts: "Archive",
    plugins: "My Works",
    legal: "Legal Market",
    market: "Neural Market",
    forge: "The Forge",
    plaza: "Plaza",
    console: "Console",
    workspace: "Workspace",
    fleet: "Fleet",
    pair: "Add Agent",
  },
} as const;

/** 落地页 Hero 按钮、下载、登录等 */
export const nexusLanding = {
  zh: {
    download: "桌面端下载",
    primaryBtn: "进入神经集市",
    secondaryBtn: "启动锻造炉",
    login: "登录",
    signOut: "退出",
    loggedInFallback: "已登录",
  },
  en: {
    download: "Download Desktop",
    primaryBtn: "Enter Neural Market",
    secondaryBtn: "Launch The Forge",
    login: "Sign in",
    signOut: "Sign out",
    loggedInFallback: "Signed in",
  },
} as const;

/** 企业主审计大屏 */
export const nexusAnalytics = {
  zh: {
    title: "企业主审计大屏",
    subtitle: "用量趋势 · 活跃技能 · 全局成功率",
    range24h: "24 小时",
    range7d: "7 天",
    totalCalls: "总调用量",
    activeSkills: "活跃技能",
    successLatency: "全局成功率 / 平均耗时",
    usageTrend: "用量趋势",
    skillRank: "活跃技能排名",
    noData: "暂无数据",
    tooltipCalls: "调用量",
    tooltipInvoke: "调用",
  },
  en: {
    title: "Tenant Audit Dashboard",
    subtitle: "Usage trends · Active skills · Global success rate",
    range24h: "24 hours",
    range7d: "7 days",
    totalCalls: "Total invocations",
    activeSkills: "Active skills",
    successLatency: "Success rate / Avg latency",
    usageTrend: "Usage trend",
    skillRank: "Active skill ranking",
    noData: "No data yet",
    tooltipCalls: "Calls",
    tooltipInvoke: "Invocations",
  },
} as const;

/** /developer/payouts 开发者收益中心 */
export const nexusDeveloperPayouts = {
  zh: {
    title: "开发者收益中心",
    subtitle: "总调用量 · 待结算余额 · 应用表现",
    totalCalls: "总调用量",
    pendingBalance: "待结算余额",
    rateHint: "1000 次 ≈ ¥1",
    settled: "已结算",
    appList: "应用列表",
    colApp: "应用",
    colCalls: "调用量",
    colPending: "待结算",
    colSuccess: "成功率",
    colLatency: "平均耗时",
    empty: "暂无应用数据，发布 Skill/MCP 后即可在此查看收益",
  },
  en: {
    title: "Developer earnings",
    subtitle: "Total invocations · Pending balance · App performance",
    totalCalls: "Total invocations",
    pendingBalance: "Pending balance",
    rateHint: "~¥1 per 1000 invocations",
    settled: "Settled",
    appList: "Applications",
    colApp: "App",
    colCalls: "Calls",
    colPending: "Pending",
    colSuccess: "Success rate",
    colLatency: "Avg latency",
    empty: "No apps yet. Publish a Skill or MCP to see earnings here.",
  },
} as const;

/** /developer/plugins 我的作品 */
export const nexusDeveloperPlugins = {
  zh: {
    title: "我的作品",
    subtitle: "管理已发布的 Skill / MCP，可自助下架",
    placeholder: "开发者 ID（如 dev-demo-001 或 publish 时填写的 developer_id）",
    view: "查看",
    devIdLabel: "开发者 ID:",
    switch: "切换",
    emptyPrompt: "请输入开发者 ID 查看作品",
    emptyHint: "publish 时填写的 developer_id，或与收益中心一致的 ID",
    noPlugins: "暂无作品",
    noPluginsHint: "使用 jachin publish 发布后，将在此展示",
    statusApproved: "已上架",
    statusArchived: "已归档",
    unpublish: "下架",
    unpublishing: "处理中...",
    archivedNote: "下架后需联系管理员恢复",
    toastEnterId: "请先输入开发者 ID",
    toastLoadFail: "加载失败",
    toastEnterIdError: "请输入开发者 ID",
    toastUnlockFirst: "请先输入开发者 ID 解锁",
    toastUnpublishOk: "插件已下架归档",
    toastUnpublishFail: "下架失败",
    toastNetwork: "网络错误",
  },
  en: {
    title: "My works",
    subtitle: "Manage published Skills / MCPs — unpublish yourself",
    placeholder: "Developer ID (e.g. dev-demo-001 or developer_id from publish)",
    view: "Load",
    devIdLabel: "Developer ID:",
    switch: "Change",
    emptyPrompt: "Enter your developer ID to list works",
    emptyHint: "Same developer_id as in publish, or as in the earnings page",
    noPlugins: "No works yet",
    noPluginsHint: "After jachin publish, items will appear here",
    statusApproved: "Live",
    statusArchived: "Archived",
    unpublish: "Unpublish",
    unpublishing: "Working…",
    archivedNote: "Contact admin to restore after unpublish",
    toastEnterId: "Enter developer ID first",
    toastLoadFail: "Failed to load",
    toastEnterIdError: "Enter developer ID",
    toastUnlockFirst: "Enter developer ID first",
    toastUnpublishOk: "Plugin archived",
    toastUnpublishFail: "Unpublish failed",
    toastNetwork: "Network error",
  },
} as const;

/** /store 应用商店 */
export const nexusStore = {
  zh: {
    subtitle: "云边协同数字发行 · 终端技能、底层驱动与原子工具",
    tabSkill: "⚡ 终端技能 (Skills)",
    tabMcp: "🔌 底层驱动 (MCPs)",
    tabTool: "🔧 原子工具 (Tools)",
    /** 与四大原语一致：core:/util:/jpp: 为宿主/ Wasm 原子工具；MCP 为独立进程/协议扩展，见上一标签 */
    toolTabExplain:
      "说明：本页为「原子工具」——含 L3 内置的 core:/util:/ 以及 Wasm 技能暴露的 jpp:*（如行情、透析镜原子）。「底层驱动」页才是 MCP（stdio/协议扩展进程），二者不要混为一类。",
    loading: "加载中...",
    totalItems: (n: number) => `共 ${n} 件商品`,
    noDesc: "暂无描述",
    perMonth: "/ 月",
    free: "免费 / Free",
    subscribing: "订阅中...",
    owned: "✓ 已拥有",
    getSubscribe: "获取 / 订阅",
    emptySkill: "暂无终端技能",
    emptyMcp: "暂无底层驱动",
    emptyTool: "暂无原子工具",
    emptyHint: "商城货架为空，敬请期待创作者上架新商品",
    toastAlreadyOwned: "您已拥有该物资",
    toastSubscribeFail: "订阅失败，请重试",
    toastSubscribeOk: "订阅成功！边缘网关(L2)即将开始自动空投装配。",
    toastNetwork: "网络错误，请重试",
    toolBuiltinBadge: "L3 内置",
    toolPreinstalled: "已预装",
    toolBuiltinHint: "随终端运行时加载，无需订阅",
  },
  en: {
    subtitle: "Edge–cloud distribution · Skills, MCP drivers & atomic tools",
    tabSkill: "⚡ Skills",
    tabMcp: "🔌 MCPs",
    tabTool: "🔧 Tools",
    toolTabExplain:
      "This tab lists atomic tools: native core:/util:/ and jpp:* exposed by Wasm skills (e.g. market data). The MCP tab is for protocol/extension processes — different category.",
    loading: "Loading…",
    totalItems: (n: number) => `${n} item(s)`,
    noDesc: "No description",
    perMonth: "/ mo",
    free: "Free",
    subscribing: "Subscribing…",
    owned: "✓ Owned",
    getSubscribe: "Get / Subscribe",
    emptySkill: "No skills yet",
    emptyMcp: "No MCPs yet",
    emptyTool: "No tools yet",
    emptyHint: "The store is empty — new listings coming soon",
    toastAlreadyOwned: "You already own this item",
    toastSubscribeFail: "Subscription failed — try again",
    toastSubscribeOk: "Subscribed. Edge gateway (L2) will sync shortly.",
    toastNetwork: "Network error — try again",
    toolBuiltinBadge: "L3 built-in",
    toolPreinstalled: "Pre-installed",
    toolBuiltinHint: "Ships with the runtime — no subscription",
  },
} as const;

/** /market 神经集市 — 分类与面板文案 */
export const nexusMarket = {
  zh: {
    heroSub: "悬浮在深空中的赛博朋克神经元星图",
    loadingNodes: "加载神经元...",
    noPlugins: "暂无插件",
    noDesc: "暂无描述",
    deploys: "次部署",
    deployTarget: "部署目标",
    deployIng: "部署中...",
    deploySent: "✓ 指令已下发",
    deployCta: "部署到边缘智能体",
    close: "关闭",
    placeholderTitle: "点击左侧神经元节点",
    placeholderSub: "查看 JMP 2.0 Manifest 并部署到私有大脑",
    catSkill: "左脑能力",
    catPersona: "右脑灵魂",
    catMemory: "海马体记忆",
    catDefault: "插件",
  },
  en: {
    heroSub: "Cyberpunk neuron map in deep space",
    loadingNodes: "Loading neurons…",
    noPlugins: "No plugins",
    noDesc: "No description",
    deploys: "deployments",
    deployTarget: "Deploy target",
    deployIng: "Deploying…",
    deploySent: "✓ Command sent",
    deployCta: "Deploy to edge agent",
    close: "Close",
    placeholderTitle: "Select a neuron on the left",
    placeholderSub: "View JMP 2.0 manifest and deploy to your private brain",
    catSkill: "Left-brain skill",
    catPersona: "Right-brain persona",
    catMemory: "Hippocampus memory",
    catDefault: "Plugin",
  },
} as const;

function marketCategoryKey(cat: string): keyof typeof nexusMarket.zh {
  const c = cat.toLowerCase();
  if (c === "skill") return "catSkill";
  if (c === "persona") return "catPersona";
  if (c === "memory") return "catMemory";
  return "catDefault";
}

export function nexusMarketCategoryLabel(lang: NexusUiLang, category: string): string {
  const k = marketCategoryKey(category);
  return nexusMarket[lang][k];
}

/** /plaza 广场 */
export const nexusPlaza = {
  zh: {
    title: "神经元广场",
    subtitle: "AI 时代的「魔法师抄作业」视觉盛宴 · 复合蓝图展示与一键 Fork",
    deploy: "⚡ 部署至边缘智能体",
    fork: "🧬 一键 Fork",
  },
  en: {
    title: "Neuron plaza",
    subtitle: "Blueprint showcase & one-click fork — AI-era inspiration wall",
    deploy: "⚡ Deploy to edge agent",
    fork: "🧬 Fork",
  },
} as const;

/** Plaza 顶部滚动 Mock 日志（英文界面用英文占位） */
export const nexusPlazaMockLogs = {
  zh: [
    "[1分钟前] 边缘智能体 0x8A9F 成功过滤 1000 条恶意指令",
    "[2分钟前] 蓝图 enterprise-legal-v1 被 Fork 23 次",
    "[3分钟前] 边缘智能体 0x3B2C 完成 RAG 索引更新",
    "[5分钟前] 新蓝图「傲娇女仆语音包」上架 Neural Market",
    "[8分钟前] 边缘智能体 0x7D1E 心跳正常，0 次上传",
    "[10分钟前] 魔法师 @prompt_mage 发布《企业级高管私人助理 v1.0》",
    "[12分钟前] 悬赏任务 #B042 已被极客接单",
    "[15分钟前] 边缘智能体 0x8A9F 成功过滤 1000 条恶意指令",
  ],
  en: [
    "[1m ago] Edge agent 0x8A9F filtered 1000 malicious commands",
    "[2m ago] Blueprint enterprise-legal-v1 forked 23 times",
    "[3m ago] Edge agent 0x3B2C finished RAG index update",
    "[5m ago] New blueprint listed on Neural Market",
    "[8m ago] Edge agent 0x7D1E heartbeat OK, 0 uploads",
    "[10m ago] @prompt_mage published Executive Assistant v1.0",
    "[12m ago] Bounty #B042 claimed",
    "[15m ago] Edge agent 0x8A9F filtered 1000 malicious commands",
  ],
} as const;

/** /forge 锻造炉 */
export const nexusForge = {
  zh: {
    paletteTitle: "神经元组件库",
    paletteHint: "拖拽到画布放置",
    minting: "铸造中…",
    mintCta: "⚡ 铸造并上架蓝图 (Compile & Mint Blueprint)",
    mintFail: "铸造失败",
    mintOk: "AST 语法树已成功写入底层数据库！版税资产已确权！",
    mintNetwork: "网络请求失败",
    defaultName: "Forge 蓝图",
    toastDefault: "AST 语法树已生成！版税分润链路已锁定！",
    nodes: [
      { id: "n1", label: "麦克风语音唤醒" },
      { id: "n2", label: "意图分析 LLM" },
      { id: "n3", label: "扬声器播放" },
    ],
    palette: [
      { type: "trigger" as const, label: "麦克风语音唤醒", pluginId: "geek-a-wake", price: "$1" },
      { type: "trigger" as const, label: "HTTP Webhook", pluginId: "geek-b-webhook", price: "$2" },
      { type: "processor" as const, label: "本地离线 LLM", pluginId: "geek-c-llm", price: "$5" },
      { type: "processor" as const, label: "情感分析 WASM", pluginId: "geek-d-wasm", price: "$3" },
      { type: "action" as const, label: "扬声器播放", pluginId: "geek-e-tts", price: "$2" },
      { type: "action" as const, label: "控制 IoT 继电器", pluginId: "geek-f-iot", price: "$4" },
    ],
  },
  en: {
    paletteTitle: "Neuron palette",
    paletteHint: "Drag onto canvas",
    minting: "Minting…",
    mintCta: "⚡ Compile & mint blueprint",
    mintFail: "Mint failed",
    mintOk: "AST saved. Royalty chain locked.",
    mintNetwork: "Network error",
    defaultName: "Forge blueprint",
    toastDefault: "AST generated. Royalty pipeline locked.",
    nodes: [
      { id: "n1", label: "Mic wake word" },
      { id: "n2", label: "Intent LLM" },
      { id: "n3", label: "Speaker playback" },
    ],
    palette: [
      { type: "trigger" as const, label: "Mic wake word", pluginId: "geek-a-wake", price: "$1" },
      { type: "trigger" as const, label: "HTTP Webhook", pluginId: "geek-b-webhook", price: "$2" },
      { type: "processor" as const, label: "Local offline LLM", pluginId: "geek-c-llm", price: "$5" },
      { type: "processor" as const, label: "Sentiment WASM", pluginId: "geek-d-wasm", price: "$3" },
      { type: "action" as const, label: "Speaker playback", pluginId: "geek-e-tts", price: "$2" },
      { type: "action" as const, label: "IoT relay control", pluginId: "geek-f-iot", price: "$4" },
    ],
  },
} as const;

/** 登录 / 注册页 */
export const nexusLogin = {
  zh: {
    subtitleLogin: "登录以继续",
    subtitleRegister: "注册账号（登录后请创建或加入工作区）",
    tabLogin: "登录",
    tabRegister: "注册",
    labelDisplayName: "显示名（可选）",
    placeholderNick: "昵称",
    labelEmail: "邮箱",
    labelPassword: "密码",
    placeholderPwdRegister: "至少 8 位",
    placeholderPwdLogin: "••••••••",
    submitLogin: "登录",
    submitRegister: "注册并登录",
    backHome: "返回首页",
    errBadCreds: "邮箱或密码错误",
    errConfig:
      "登录服务配置异常（例如生产环境未设置 AUTH_SECRET）。请检查 Nexus 环境变量。",
    errLoginRetry: "登录失败，请重试",
    errRegister: "注册失败",
    errRegisterRetry: "注册失败，请重试",
    errRegisterAutoLogin: (detail: string) =>
      `注册成功但自动登录失败：${detail} 请尝试手动登录。`,
    errSignInFailed: (code: string) =>
      `登录失败（${code}）。若刚配置 DATABASE_URL，请重启 npm run dev 并确认 PostgreSQL 已启动。`,
  },
  en: {
    subtitleLogin: "Sign in to continue",
    subtitleRegister: "Create an account (then create or join a workspace)",
    tabLogin: "Sign in",
    tabRegister: "Register",
    labelDisplayName: "Display name (optional)",
    placeholderNick: "Nickname",
    labelEmail: "Email",
    labelPassword: "Password",
    placeholderPwdRegister: "At least 8 characters",
    placeholderPwdLogin: "••••••••",
    submitLogin: "Sign in",
    submitRegister: "Register & sign in",
    backHome: "Back to home",
    errBadCreds: "Invalid email or password",
    errConfig:
      "Sign-in is misconfigured (e.g. AUTH_SECRET missing in production). Check Nexus env.",
    errLoginRetry: "Sign-in failed. Please try again.",
    errRegister: "Registration failed",
    errRegisterRetry: "Registration failed. Please try again.",
    errRegisterAutoLogin: (detail: string) =>
      `Account created but auto sign-in failed: ${detail} Try signing in manually.`,
    errSignInFailed: (code: string) =>
      `Sign-in failed (${code}). If you just set DATABASE_URL, restart the dev server and ensure PostgreSQL is running.`,
  },
} as const;

export function signInFailureMessageI18n(
  lang: NexusUiLang,
  error: string | undefined
): string {
  const t = nexusLogin[lang];
  if (!error || error === "CredentialsSignin") {
    return t.errBadCreds;
  }
  if (error === "Configuration") {
    return t.errConfig;
  }
  return t.errSignInFailed(error);
}

/** /console 指挥台 */
export const nexusConsole = {
  zh: {
    blueprintArmory: "蓝图武库",
    blueprintHint: "拖拽到右侧智能体卡片上完成部署",
    agentMapTitle: "边缘智能体星图",
    linkWorkspace: "工作区与权限",
    linkFleet: "舰队指挥大屏 →",
    deploying: "📡 正在热更新蓝图...",
    offlineAgent: "边缘智能体已离线",
    currentBlueprint: "当前蓝图",
    toastDeployOk: "✅ 蓝图已成功下发至边缘智能体！",
    defaultAgentName: "边缘智能体",
    forgeBlueprintDesc: "Forge 蓝图",
  },
  en: {
    blueprintArmory: "Blueprint armory",
    blueprintHint: "Drag onto an agent card to deploy",
    agentMapTitle: "Edge agent map",
    linkWorkspace: "Workspace & access",
    linkFleet: "Fleet command →",
    deploying: "📡 Deploying blueprint…",
    offlineAgent: "Edge agent offline",
    currentBlueprint: "Current blueprint",
    toastDeployOk: "✅ Blueprint deployed to the edge agent!",
    defaultAgentName: "Edge agent",
    forgeBlueprintDesc: "Forge blueprint",
  },
} as const;

export const nexusConsoleFallbackBlueprints = {
  zh: [
    { id: "bp-1", name: "离线医疗助手", desc: "本地诊断推理" },
    { id: "bp-2", name: "傲娇女仆客服", desc: "语音对话服务" },
    { id: "bp-3", name: "安防视觉中枢", desc: "实时视频分析" },
  ],
  en: [
    { id: "bp-1", name: "Offline medical assistant", desc: "On-device diagnostic inference" },
    { id: "bp-2", name: "Tsundere maid support", desc: "Voice dialogue service" },
    { id: "bp-3", name: "Security vision hub", desc: "Live video analytics" },
  ],
} as const;

export const nexusConsoleFallbackAgents = {
  zh: [
    { id: "agent-1", name: "多伦多一号机", blueprint: "离线医疗助手" },
    { id: "agent-2", name: "树莓派测试节点", blueprint: "傲娇女仆客服" },
    { id: "agent-3", name: "上海门店终端", blueprint: "—" },
  ],
  en: [
    { id: "agent-1", name: "Toronto node-1", blueprint: "Offline medical assistant" },
    { id: "agent-2", name: "Raspberry Pi test", blueprint: "Tsundere maid support" },
    { id: "agent-3", name: "Shanghai store terminal", blueprint: "—" },
  ],
} as const;

/** /dashboard/admin/review Legal Market 法律审核 */
export const nexusLegalReview = {
  zh: {
    tokenInvalid: "Token 无效，请检查 NEXUS_ADMIN_SECRET 配置",
    unlockTitle: "管理员验证",
    unlockDesc: "此页面仅限 isRoot 用户访问。请输入管理员 Token 解锁。",
    unlockBtn: "解锁",
    title: "法律审核中心",
    subtitle: "待审插件 · 已上架管理 · 已归档恢复 · 仅 isRoot",
    tabPending: "待审",
    tabApproved: "已上架",
    tabArchived: "已归档",
    emptyPending: "暂无待审插件",
    emptyApproved: "暂无已上架插件",
    emptyArchived: "暂无已归档插件",
    emptyHintPending: "开发者发布 PUBLIC 插件后将在此排队",
    emptyHintApproved: "批准后的插件将在此展示，可下架归档",
    emptyHintArchived: "下架后的插件将在此展示，可恢复上架",
    devId: "开发者 ID:",
    selectLeft: "选择左侧插件查看详情",
    approve: "批准入驻",
    reject: "驳回申请",
    archive: "下架归档",
    restore: "恢复上架",
    permissions: "权限声明",
    noExtraPerms: "无特殊权限",
    pluginJsonTitle: "plugin.json 完整内容",
    rejectModalTitle: "驳回申请",
    rejectModalPlugin: "插件：",
    rejectPlaceholder: "驳回理由（可选，将通知开发者）",
    cancel: "取消",
    confirmReject: "确认驳回",
    processing: "处理中...",
    toastArchiveOk: "插件已下架归档，商城与 manifest 均不再展示",
    toastRestoreOk: "插件已恢复上架",
    toastApproveOk: "✅ 该物资已面向全球 L2 开放同步",
    toastRejectOk: "已驳回该插件请求。",
    failArchive: (e: string) => `下架失败：${e}`,
    failRestore: (e: string) => `恢复失败：${e}`,
    failApprove: (e: string) => `批准失败：${e}`,
    failReject: (e: string) => `驳回失败：${e}`,
    unknownError: "未知错误",
    networkError: "网络错误",
    priceMonthly: (n: number) => `¥${n}/月`,
  },
  en: {
    tokenInvalid: "Invalid token. Check NEXUS_ADMIN_SECRET.",
    unlockTitle: "Admin verification",
    unlockDesc: "Root only. Enter the admin token to unlock.",
    unlockBtn: "Unlock",
    title: "Legal review",
    subtitle: "Pending · Live · Archived · root only",
    tabPending: "Pending",
    tabApproved: "Live",
    tabArchived: "Archived",
    emptyPending: "No pending plugins",
    emptyApproved: "No live plugins",
    emptyArchived: "No archived plugins",
    emptyHintPending: "PUBLIC submissions from developers appear here",
    emptyHintApproved: "Approved plugins are listed here; you can archive",
    emptyHintArchived: "Archived plugins appear here; you can restore",
    devId: "Developer ID:",
    selectLeft: "Select a plugin on the left",
    approve: "Approve",
    reject: "Reject",
    archive: "Archive",
    restore: "Restore",
    permissions: "Permissions",
    noExtraPerms: "No extra permissions",
    pluginJsonTitle: "Full plugin.json",
    rejectModalTitle: "Reject submission",
    rejectModalPlugin: "Plugin:",
    rejectPlaceholder: "Reason (optional; may be shown to the developer)",
    cancel: "Cancel",
    confirmReject: "Confirm reject",
    processing: "Working…",
    toastArchiveOk: "Plugin archived; store and manifest updated",
    toastRestoreOk: "Plugin restored to live",
    toastApproveOk: "✅ Plugin synced to global L2",
    toastRejectOk: "Submission rejected.",
    failArchive: (e: string) => `Archive failed: ${e}`,
    failRestore: (e: string) => `Restore failed: ${e}`,
    failApprove: (e: string) => `Approve failed: ${e}`,
    failReject: (e: string) => `Reject failed: ${e}`,
    unknownError: "Unknown error",
    networkError: "Network error",
    priceMonthly: (n: number) => `¥${n}/mo`,
  },
} as const;

/** /plaza Mock 卡片（名称等随语言切换） */
export const nexusPlazaMockBlueprints = {
  zh: [
    { id: "1", name: "企业级高管私人助理 v1.0", deploys: 12500, author: "prompt_mage", avatar: "🧙", sbt: "金牌魔法师" },
    { id: "2", name: "低成本离线智慧门店方案", deploys: 8200, author: "edge_architect", avatar: "🏗️", sbt: "蓝图架构师" },
    { id: "3", name: "全自动 AI 心理医生", deploys: 5600, author: "flow_composer", avatar: "🎭", sbt: "灵魂注入者" },
    { id: "4", name: "傲娇女仆语音包", deploys: 18900, author: "vits_master", avatar: "🎤", sbt: "声纹雕刻师" },
    { id: "5", name: "少儿英语外教蓝图", deploys: 4200, author: "edu_wizard", avatar: "📚", sbt: "教育魔法师" },
    { id: "6", name: "自动挂断诈骗电话 AI 路由器", deploys: 3100, author: "security_geek", avatar: "🛡️", sbt: "防线守卫" },
  ],
  en: [
    { id: "1", name: "Executive assistant v1.0", deploys: 12500, author: "prompt_mage", avatar: "🧙", sbt: "Gold mage" },
    { id: "2", name: "Low-cost offline smart retail", deploys: 8200, author: "edge_architect", avatar: "🏗️", sbt: "Blueprint architect" },
    { id: "3", name: "AI therapist blueprint", deploys: 5600, author: "flow_composer", avatar: "🎭", sbt: "Soul injector" },
    { id: "4", name: "Tsundere maid voice pack", deploys: 18900, author: "vits_master", avatar: "🎤", sbt: "Voice sculptor" },
    { id: "5", name: "Kids English tutor blueprint", deploys: 4200, author: "edu_wizard", avatar: "📚", sbt: "Edu mage" },
    { id: "6", name: "Scam-call hangup AI router", deploys: 3100, author: "security_geek", avatar: "🛡️", sbt: "Perimeter guard" },
  ],
} as const;

/** 组织角色文案（工作区页） */
export const nexusOrgRoleUi = {
  zh: {
    labels: {
      owner: "所有者",
      admin: "管理员",
      member: "成员",
      fleet_admin: "车队管理员",
      viewer: "只读",
    },
    descriptions: {
      owner:
        "组织最高权限：管理成员与角色、发放邀请、车队与订阅边界以租户为准；个人默认工作区创建即为所有者。",
      admin:
        "可管理成员（除产生第二个 owner）、发放邀请；车队与商店等写操作通常可用（具体以接口校验为准）。",
      member: "正式成员：可访问当前工作区下已授权的控制台能力；敏感管理操作受限。",
      fleet_admin:
        "侧重边缘设备 / 车队：可管理配对范围内的设备与部署；组织级计费与成员管理通常受限。",
      viewer: "只读：可查看成员列表、舰队状态等，不可改角色或下发变更。",
    },
    deviceGroup: {
      admin: "设备组管理员",
      viewer: "设备组只读",
    },
  },
  en: {
    labels: {
      owner: "Owner",
      admin: "Admin",
      member: "Member",
      fleet_admin: "Fleet admin",
      viewer: "Viewer",
    },
    descriptions: {
      owner:
        "Top org role: manage members and invites; fleet and billing are scoped to the tenant; personal default workspace starts as owner.",
      admin:
        "Can manage members (except adding a second owner) and send invites; fleet/store writes usually allowed (API may enforce more).",
      member:
        "Standard access to authorized console features in this workspace; sensitive admin actions restricted.",
      fleet_admin:
        "Focus on edge/fleet: manage paired devices and deployments; org billing/member admin often limited.",
      viewer: "Read-only: view members and fleet; cannot change roles or push changes.",
    },
    deviceGroup: {
      admin: "Device group admin",
      viewer: "Device group viewer",
    },
  },
} as const;

export function formatOrgRoleI18n(
  lang: NexusUiLang,
  role: string | undefined | null
): string {
  if (!role) return "—";
  const labels = nexusOrgRoleUi[lang].labels as Record<string, string>;
  return labels[role] ?? role;
}

export function formatDeviceGroupRoleI18n(
  lang: NexusUiLang,
  role: string | undefined | null
): string {
  if (!role) return "—";
  const dg = nexusOrgRoleUi[lang].deviceGroup;
  return (dg as Record<string, string>)[role] ?? role;
}

/** /console/workspace */
export const nexusWorkspace = {
  zh: {
    errLoadOrgs: "无法加载组织列表",
    errNetwork: "网络错误，无法加载工作区",
    successInvitePrefill: "已从邀请链接填入 Token，确认后点击下方「加入工作区」。",
    errCreate: "创建工作区失败",
    successCreate: (name: string) =>
      `已创建工作区「${name}」。可切换过去或继续邀请成员。`,
    errJoin: "加入失败，请检查 Token 是否过期或已使用",
    joinAlreadyMember: "你已是该工作区成员。",
    joinSuccess:
      "已成功加入工作区。可点击下方按钮切换上下文，或稍后在列表中切换。",
    errJoinGeneric: "加入工作区失败",
    errInviteGen: "无法生成邀请（需为所有者或管理员，且当前会话在工作区内）",
    successInviteGen:
      "邀请已生成，请将 Token 或链接发给对方；对方需登录后在「加入工作区」中粘贴或打开链接。",
    errInviteFailed: "生成邀请失败",
    errCopy: "复制失败，请手动选择文本复制",
    errSwitch: "切换工作区失败",
    loginPrompt: "请先登录以查看工作区与权限。",
    goLogin: "去登录",
    title: "工作区与权限",
    intro:
      "租户边界来自组织（工作区）；会话内 org_id 决定商店同步、舰队与 API 数据范围。边缘设备（L3）向 L2 配对时须填写与当前 L2 绑定的同一 organization_id（可在下方列表复制）；可用 GET /api/v1/me/workspaces 供设备端下拉。",
    backConsole: "返回指挥台",
    onboardingTitle: "请先创建或加入工作区",
    onboardingBody:
      "新账号注册后不会自动拥有组织。请在本页创建团队工作区，或通过邀请加入；完成后即可使用商店、舰队、以及使用 L1 邮箱登录 L2 网关（须为工作区所有者或管理员）。",
    createTitle: "创建团队工作区",
    createDesc:
      "新建独立租户，你将成为所有者。注册流程不再自动创建「个人工作区」，此处为首选入口。",
    phWorkspaceName: "工作区显示名称",
    createSubmit: "创建并切换",
    joinTitle: "通过邀请加入工作区",
    joinDesc:
      "向工作区管理员索取邀请 Token，或打开对方发来的邀请链接（将自动填入）。加入后需切换工作区方可访问该租户数据。",
    phJoinToken: "粘贴邀请 JWT…",
    joinBtn: "加入工作区",
    switchJoined: "切换到刚加入的工作区",
    inviteTitle: "邀请他人加入当前工作区",
    inviteIntroBefore: "基于当前会话工作区",
    unknownOrg: "（未知）",
    inviteIntroAfter:
      "签发短效邀请。对方须登录本站的账号后使用 Token 或链接加入；无法通过邀请成为所有者。",
    labelInviteRole: "加入后的角色",
    labelTtl: "有效期",
    generateInvite: "生成邀请",
    tokenHelp: "邀请 Token（整段复制给对方）",
    copyToken: "复制 Token",
    copyLink: "复制邀请链接",
    linkHashHint:
      "链接使用 URL 哈希携带 Token，不会发送到服务器访问日志；若链接过长，请改用复制 Token。",
    noInvite:
      "你在当前工作区内的角色为「{role}」，无权发放邀请；需所有者或管理员操作。",
    accountTitle: "当前账号",
    userId: "用户 ID",
    emailName: "邮箱 / 名称",
    sessionOrgId: "会话内工作区 ID",
    orgRoleHere: "在当前工作区内的组织角色",
    orgListTitle: "工作区（组织）",
    loading: "加载中…",
    orgEmpty: "暂无组织数据（请确认已配置数据库并完成注册）。",
    myRoleLine: "我在此工作区：",
    personalDefault: "· 个人默认工作区",
    current: "当前",
    switchOrg: "切换到此工作区",
    footerOrgScope: (name: string) => `当前展示的成员与设备组均属于「${name}」。`,
    membersTitle: "当前工作区成员",
    membersEmpty: "暂无成员或无权查看（需登录且会话含 org）。",
    thMember: "成员",
    thOrgRole: "组织角色",
    thJoined: "加入时间",
    groupsTitle: "设备组（车队 / 站点）",
    groupsIntro:
      "组级权限在租户角色之下做细粒度覆写；未列入组时，以组织角色为准。",
    groupsEmptyPrefix:
      "当前工作区下尚无设备组，或设备尚未归属到组。可在数据层创建 ",
    groupsEmptySuffix: " 后在此查看。",
    deviceCount: "设备数",
    myGroupRole: "我的组内角色：",
    groupRoleFallback: "未单独授权（沿用组织角色）",
    rolesMatrixTitle: "组织角色说明（参考）",
    inviteTtl: [
      { label: "15 分钟", sec: 900 },
      { label: "1 小时", sec: 3600 },
      { label: "24 小时", sec: 86400 },
      { label: "7 天", sec: 7 * 86400 },
    ],
  },
  en: {
    errLoadOrgs: "Could not load organizations",
    errNetwork: "Network error while loading workspace",
    successInvitePrefill:
      "Invite token filled from the link. Confirm below to join the workspace.",
    errCreate: "Could not create workspace",
    successCreate: (name: string) =>
      `Workspace “${name}” created. Switch to it or invite members.`,
    errJoin: "Could not join — token may be expired or already used",
    joinAlreadyMember: "You are already a member of this workspace.",
    joinSuccess:
      "Joined successfully. Use the button below to switch context, or switch later from the list.",
    errJoinGeneric: "Failed to join workspace",
    errInviteGen:
      "Cannot generate invite (need owner or admin, and session must be in a workspace)",
    successInviteGen:
      "Invite created. Send the token or link; the other person signs in and uses Join workspace.",
    errInviteFailed: "Failed to generate invite",
    errCopy: "Copy failed — select text manually",
    errSwitch: "Failed to switch workspace",
    loginPrompt: "Sign in to view workspace and permissions.",
    goLogin: "Sign in",
    title: "Workspace & access",
    intro:
      "Tenants are workspaces; session org_id scopes store sync, fleet, and APIs. Edge (L3) pairs to L2 using the same organization_id as below (copy from the list). Devices can use GET /api/v1/me/workspaces.",
    backConsole: "Back to console",
    onboardingTitle: "Create or join a workspace",
    onboardingBody:
      "New accounts have no org by default. Create a team workspace here or join via invite; then use Store, Fleet, and L2 gateway with your L1 email (as owner or admin).",
    createTitle: "Create team workspace",
    createDesc:
      "Creates a new tenant; you become owner. Personal workspaces are not auto-created — start here.",
    phWorkspaceName: "Workspace display name",
    createSubmit: "Create & switch",
    joinTitle: "Join via invite",
    joinDesc:
      "Ask an admin for a token or open their invite link (auto-fills). Switch workspace after joining to access that tenant.",
    phJoinToken: "Paste invite JWT…",
    joinBtn: "Join workspace",
    switchJoined: "Switch to joined workspace",
    inviteTitle: "Invite to current workspace",
    inviteIntroBefore: "For workspace",
    unknownOrg: "(unknown)",
    inviteIntroAfter:
      "— create a short-lived invite. The invitee must sign in here and use Join workspace or the token; owner cannot be granted via invite.",
    labelInviteRole: "Role after join",
    labelTtl: "Expires in",
    generateInvite: "Generate invite",
    tokenHelp: "Invite token (copy whole string)",
    copyToken: "Copy token",
    copyLink: "Copy invite link",
    linkHashHint:
      "Link carries the token in the URL hash (not server logs). If the link is too long, copy the token instead.",
    noInvite:
      "Your role is “{role}” — you cannot send invites. Owner or admin required.",
    accountTitle: "Account",
    userId: "User ID",
    emailName: "Email / name",
    sessionOrgId: "Workspace ID in session",
    orgRoleHere: "Org role in current workspace",
    orgListTitle: "Workspaces (orgs)",
    loading: "Loading…",
    orgEmpty: "No organizations (check DB and that you completed signup).",
    myRoleLine: "Your role:",
    personalDefault: "· Personal default",
    current: "Current",
    switchOrg: "Switch here",
    footerOrgScope: (name: string) => `Members and device groups below are for “${name}”.`,
    membersTitle: "Members",
    membersEmpty: "No members or no access (need signed-in session with org).",
    thMember: "Member",
    thOrgRole: "Org role",
    thJoined: "Joined",
    groupsTitle: "Device groups (fleet / site)",
    groupsIntro:
      "Group roles override org roles where set; otherwise org role applies.",
    groupsEmptyPrefix: "No device groups yet, or devices not assigned. Create ",
    groupsEmptySuffix: " in the data layer to see them here.",
    deviceCount: "Devices",
    myGroupRole: "My group role:",
    groupRoleFallback: "No group override (use org role)",
    rolesMatrixTitle: "Org roles (reference)",
    inviteTtl: [
      { label: "15 min", sec: 900 },
      { label: "1 hour", sec: 3600 },
      { label: "24 hours", sec: 86400 },
      { label: "7 days", sec: 7 * 86400 },
    ],
  },
} as const;

export function orgRoleDescriptionI18n(lang: NexusUiLang, role: OrgRole): string {
  return nexusOrgRoleUi[lang].descriptions[role];
}
