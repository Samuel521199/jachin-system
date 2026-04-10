import type { ComponentType } from "react";

/** 内联气泡 vs 右侧画布（Artifacts 式） */
export type SkillUiDisplayMode = "inline" | "canvas";

/**
 * 每个注册到 SkillUIRegistry 的面板组件收到的统一 Props。
 * 与具体 Tool 的 args 结构解耦：由各组件自行从 args 中解析字段。
 */
export interface SkillUiPanelProps {
  /** 当前工具名（与注册表 key 一致，便于组件复用） */
  toolName: string;
  /** 与 StoredMessage.tool_call.id 对齐，提交结果时回传便于后端关联 */
  toolCallId?: string;
  /** 大模型 / 后端传入的工具参数（JSON 对象） */
  args: Record<string, unknown>;
  /**
   * 用户在面板内完成选择后调用；可返回 Promise（例如等待 L3 WebSocket answer）。
   * 上层经 Sensory `tool_ui_result` 回传并由 Native 工具执行。
   */
  onToolResponse: (result: unknown) => void | Promise<void>;
  /** 由宿主注入：聊天气泡内为 inline，右侧画布为 canvas */
  layout?: SkillUiDisplayMode;
}

/** 注册表条目：组件 + 展示模式 */
export interface SkillUiRegistration {
  component: ComponentType<SkillUiPanelProps>;
  /** 默认 inline：仅在气泡内渲染；canvas 时左侧为占位卡，右侧 SkillCanvasPane 挂载同组件 */
  displayMode: SkillUiDisplayMode;
}

export type RegisteredSkillUi = ComponentType<SkillUiPanelProps>;

/** 从面板冒泡到 Chat 根组件的载荷 */
export interface ToolUiSubmitPayload {
  toolName: string;
  toolCallId?: string;
  result: unknown;
}
