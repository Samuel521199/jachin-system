/**
 * 纹理图集类型定义
 * 依据 ANIMATION_ARCHITECTURE.md §2.1 纹理图集 JSON 结构
 */

/** 帧矩形区域 {x, y, w, h} */
export interface FrameRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 单帧数据 */
export interface FrameData {
  /** 在纹理图中的位置与尺寸 */
  frame: FrameRect;
  /** 是否旋转 90° */
  rotated?: boolean;
  /** 是否裁剪透明边 */
  trimmed?: boolean;
  /** 裁剪后的源矩形（相对于原始图） */
  spriteSourceSize?: FrameRect;
  /** 原始图尺寸 */
  sourceSize: { w: number; h: number };
  /** 多图集时指向的纹理索引 */
  atlasIndex?: number;
}

/** 图集元信息 */
export interface AtlasMeta {
  /** 像素格式，如 RGBA8888 */
  format: string;
  /** 缩放比例 */
  scale: number;
  /** 默认帧率 */
  fps?: number;
  /** 纹理图片文件名列表（多图集时） */
  images: string[];
  /** 纹理尺寸 */
  size?: { w: number; h: number };
  /** 主纹理文件名（单图集时） */
  image?: string;
}

/** 动画定义 - 格式 A：帧名数组 + 顶层 frames */
export interface AnimationDefArray {
  /** 帧名序列（按播放顺序） */
  frames: string[];
  fps?: number;
  loop?: boolean;
}

/** 动画定义 - 格式 B：帧数据内嵌在动画内（如 core-atlas.json） */
export interface AnimationDefEmbedded {
  /** 帧数据对象，key 为帧名，按 key 排序得到播放顺序 */
  frames: Record<string, FrameData>;
  fps?: number;
  loop?: boolean;
}

/** 动画定义（兼容两种格式） */
export type AnimationDef = AnimationDefArray | AnimationDefEmbedded;

/** 动画定义集合，key 为大写动画名（如 IDLE, TOUCH, SLEEP） */
export type AnimationsMap = Record<string, AnimationDef>;

/** 帧数据集合，key 为原始文件名（不含扩展名） */
export type FramesMap = Record<string, FrameData>;

/** 核心图集根结构 (core-atlas.json) */
export interface CoreAtlas {
  /** 元信息 */
  meta: AtlasMeta;
  /** 帧数据（格式 A 时存在；格式 B 时无） */
  frames?: FramesMap;
  /** 动画定义 */
  animations: AnimationsMap;
}

/** 动画配置（LRU 优先级、循环等） */
export interface AnimationConfig {
  /** LRU 优先级，数值越大越优先保留 */
  priority?: number;
  /** 是否循环播放 */
  loop?: boolean;
  /** 帧率 */
  fps?: number;
}
