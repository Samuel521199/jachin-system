/**
 * 仅用于本地验证生成式 UI（Skill UI Registry）。
 * 生产构建不会引用本文件（由 chat.tsx 的 import.meta.env.DEV 静态分支保证摇树优化）。
 */

import type { StoredMessage } from "../utils/messageStorage";

/** 一条带 `tool_call` 的 assistant 气泡，工具名为注册表中的 `generate_ppt` */
export function createDemoGeneratePptSkillUiMessage(): StoredMessage {
  return {
    role: "assistant",
    content: "",
    reasoning: "",
    timestamp: Date.now(),
    source: "L3",
    tool_call: {
      name: "generate_ppt",
      id: `demo-ppt-${Date.now()}`,
      args: {
        templates: [
          { id: "pitch", label: "路演稿", description: "大图少字、强调结论" },
          { id: "training", label: "内训课件", description: "分节清晰、要点列表" },
        ],
      },
    },
  };
}

/** 演示「写作文」面板；工具名与 Native `core:compose_essay` 一致（前端匹配时忽略 core: 前缀） */
export function createDemoComposeEssaySkillUiMessage(): StoredMessage {
  return {
    role: "assistant",
    content: "",
    reasoning: "",
    timestamp: Date.now(),
    source: "L3",
    tool_call: {
      name: "core:compose_essay",
      id: `demo-essay-${Date.now()}`,
      args: {
        topic: "科技改变生活",
        topic_hint: "也可改为主题占位",
      },
    },
  };
}
