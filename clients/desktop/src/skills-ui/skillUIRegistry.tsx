/**
 * 可视化 Skill 注册表（Opt-in）
 *
 * - 仅当消息带 `tool_call` 且 `name` 在此表中有对应组件时，聊天层才会渲染自定义 UI。
 * - 未注册的工具名：走与传统 Skill 相同的弱提示路径（见 AssistantMessageContent）。
 */

import type { RegisteredSkillUi, SkillUiRegistration } from "./types";
import { PptGeneratorUI } from "./PptGeneratorUI";
import { EssayWritingUI } from "./EssayWritingUI";

/** 与 LLM / 后端对齐：支持 `compose_essay` 与 `core:compose_essay` */
export function normalizeSkillToolName(name: string): string {
  return (name || "").replace(/^core:/i, "").trim().toLowerCase();
}

/**
 * Tool 名称 → 面板注册信息（key 为小写裸名，不含 core:）。
 * 新增可视化 Skill 时：在此追加一行即可，无需改分发 if/else 结构。
 */
export const SKILL_UI_REGISTRY: Record<string, SkillUiRegistration> = {
  generate_ppt: { component: PptGeneratorUI, displayMode: "inline" },
  compose_essay: { component: EssayWritingUI, displayMode: "canvas" },
};

export function getSkillUiRegistration(toolName: string): SkillUiRegistration | undefined {
  const key = normalizeSkillToolName(toolName);
  return SKILL_UI_REGISTRY[key];
}

export function getRegisteredSkillUI(toolName: string): RegisteredSkillUi | undefined {
  return getSkillUiRegistration(toolName)?.component;
}

export function isSkillUiRegistered(toolName: string): boolean {
  return normalizeSkillToolName(toolName) in SKILL_UI_REGISTRY;
}
