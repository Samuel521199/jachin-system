/**
 * 陪伴态布局契约（SSOT）
 *
 * ⚠️ 改任何常数必须同步：
 * - `JachinOrb.tsx` 的 h/w class（ORB_SIZE_PX）
 * - `OrbWindow.tsx` 的 Tailwind padding / footer（ORB_PADDING_PX, FOOTER_HEIGHT_PX）
 * - `main.rs` 的 COMPANION_MIN_WINDOW_LOGICAL / CHAT_COMPANION_H
 *
 * 详见 docs/COMPANION_UI_REGRESSION_ROOT_CAUSE_ANALYSIS.md §六、§十三
 */

/** JachinOrb 固定尺寸（对应 h-[132px] w-[132px]） */
export const ORB_SIZE_PX = 132;

/** OrbWindow Orb 容器 Tailwind p-2 → 每边 8px */
export const ORB_PADDING_PX = 8;

/** 状态字 + gap（Orb 与 IDLE 之间 gap-1.5） */
export const STATE_TEXT_HEIGHT_PX = 22;

/** IDLE 与语音按钮间距 mt-1.5 */
export const STATE_TO_BUTTON_GAP_PX = 6;

/** 语音按钮区高度（自窗口底向上：pb-3 + 按钮 + mt-1.5），与 drag region bottom 对齐 */
export const FOOTER_HEIGHT_PX = 48;

/** 上部 flex 区 pt-2 */
export const ROOT_PT_PX = 8;

/** drop-shadow glow 视觉溢出安全量（逻辑像素） */
export const GLOW_OVERFLOW_PX = 20;

/** 内容区估算（不含 glow 安全量） */
export const CONTENT_TOTAL_PX =
  ROOT_PT_PX +
  ORB_SIZE_PX +
  ORB_PADDING_PX * 2 +
  STATE_TEXT_HEIGHT_PX +
  STATE_TO_BUTTON_GAP_PX +
  FOOTER_HEIGHT_PX +
  12; /* root pb-3 */

/** 逻辑像素窗高下限；Rust COMPANION_MIN_WINDOW_LOGICAL 须与此一致 */
export const MIN_WINDOW_LOGICAL = Math.max(260, CONTENT_TOTAL_PX + GLOW_OVERFLOW_PX);

/** sf=1.0 时默认物理窗宽（对应 main.rs CHAT_COMPANION_W） */
export const COMPANION_WINDOW_WIDTH_LOGICAL = 248;

/** sf=1.0 时默认物理窗高（含 Orb glow + 底栏余量；勿强行 320 撑空） */
export const COMPANION_WINDOW_HEIGHT_LOGICAL = MIN_WINDOW_LOGICAL;

/** 与 OrbWindow 拖拽层 bottom、快捷输入 bottom 对齐 */
export const COMPANION_VOICE_FOOTER_PX = FOOTER_HEIGHT_PX;

/** 快捷输入框距底偏移（= footer 高度） */
export const COMPANION_QUICK_INPUT_BOTTOM_PX = FOOTER_HEIGHT_PX;

/**
 * 根据实测内容高度计算陪伴窗逻辑高度下限。
 * 供 ensure_companion_window_size 传给 Rust。
 */
export function computeCompanionLogicalHeight(contentScrollHeight?: number): number {
  if (contentScrollHeight && contentScrollHeight > 0) {
    return Math.max(MIN_WINDOW_LOGICAL, Math.ceil(contentScrollHeight));
  }
  return MIN_WINDOW_LOGICAL;
}
