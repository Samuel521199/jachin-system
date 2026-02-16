/**
 * TextureAtlasManager - 纹理图集管理器
 * 职责：fetch JSON、加载 Blob 图片、解析 Frame 数据、LRU 缓存、支持 meta.images 多图集合并
 */

import {
  Texture,
  Rectangle,
  ImageSource,
  type TextureSource,
} from "pixi.js";
import type {
  CoreAtlas,
  FrameData,
  AnimationConfig,
  AnimationDefArray,
} from "@/types/animation";

/** 判断动画定义是否为「帧名数组」格式 */
function isAnimationDefArray(
  def: { frames: unknown }
): def is AnimationDefArray {
  return Array.isArray(def.frames);
}

/**
 * 解析 URL：基于当前文档 base 解析任意相对/绝对路径
 * 保证在开发、生产、Tauri、任意机器/部署位置下均能正确解析
 * @param url 图集/图片路径（来自配置或 JSON meta.images）
 * @param base 可选 base，缺省时使用 document.baseURI 或 location.href
 */
function resolveUrl(url: string, base?: string): string {
  const baseUrl =
    base ??
    ((typeof document !== "undefined" && document.baseURI) ||
      (typeof window !== "undefined" && window.location?.href) ||
      "http://localhost/");
  return new URL(url, baseUrl).href;
}

/** 获取 URL 所在目录的 base URL（末尾含 /） */
function getDirectoryUrl(url: string): string {
  const u = url.endsWith("/") ? url.slice(0, -1) : url;
  const lastSlash = u.lastIndexOf("/");
  return lastSlash >= 0 ? u.slice(0, lastSlash + 1) : u + "/";
}

/** 缓存条目：动画名 -> 纹理数组 */
interface CacheEntry {
  textures: Texture[];
  normalTextures?: Texture[];
  lastAccessTime: number;
  priority: number;
}

/** 配置选项 */
export interface TextureAtlasManagerOptions {
  /**
   * 图集 JSON 路径，支持：
   * - 相对路径："/assets/atlases/core-atlas.json"、"assets/atlases/core-atlas.json"
   * - 绝对 URL：任意部署位置
   * 图片路径由 JSON 的 meta.images 决定，相对 JSON 所在目录解析
   */
  atlasUrl: string;
  /** 法线图集 JSON 路径（可选，与主图集结构一致） */
  normalAtlasUrl?: string;
  /** LRU 缓存最大动画数量，超出时卸载最少使用的 */
  maxCacheSize?: number;
  /** 默认 LRU 优先级 */
  defaultPriority?: number;
}

/** 动画元数据（用于渲染器） */
export interface LoadedAnimationMeta {
  textures: Texture[];
  normalTextures?: Texture[];
  fps?: number;
  loop?: boolean;
}

/**
 * 纹理图集管理器
 * - 加载图集 JSON 及关联图片（路径由 JSON 位置与 meta.images 决定）
 * - 支持 meta.images 多图集合并
 * - LRU 缓存防止内存溢出
 * - 路径解析基于 document.baseURI，适配开发/生产/Tauri/任意部署位置
 */
export class TextureAtlasManager {
  private readonly atlasUrl: string;
  private readonly normalAtlasUrl?: string;
  private readonly maxCacheSize: number;
  private readonly defaultPriority: number;

  /** 原始图集数据（JSON 解析结果） */
  private atlas: CoreAtlas | null = null;
  /** 法线图集数据 */
  private normalAtlas: CoreAtlas | null = null;

  /** 图片源缓存：图片路径 -> TextureSource */
  private imageSources: Map<string, TextureSource> = new Map();
  /** 法线图集图片源缓存 */
  private normalImageSources: Map<string, TextureSource> = new Map();

  /** 动画纹理缓存：动画名 -> CacheEntry */
  private animationCache: Map<string, CacheEntry> = new Map();

  /** 动画配置覆盖（优先级、循环等） */
  private animationConfigs: Map<string, AnimationConfig> = new Map();

  /** 是否已加载图集 JSON */
  private jsonLoaded = false;
  private normalJsonLoaded = false;

  /** 解析后的图集 base URL（JSON 所在目录） */
  private resolvedAtlasBaseUrl: string | null = null;
  private resolvedNormalAtlasBaseUrl: string | null = null;

  constructor(options: TextureAtlasManagerOptions) {
    this.atlasUrl = options.atlasUrl;
    this.normalAtlasUrl = options.normalAtlasUrl;
    this.maxCacheSize = options.maxCacheSize ?? 8;
    this.defaultPriority = options.defaultPriority ?? 0;
  }

  /**
   * 加载图集 JSON（可重复调用，已加载则跳过）
   * 路径基于当前文档 base 解析，适配任意部署环境
   */
  async loadAtlasJson(): Promise<CoreAtlas> {
    if (this.jsonLoaded && this.atlas) {
      return this.atlas;
    }

    const resolvedUrl = resolveUrl(this.atlasUrl);
    const res = await fetch(resolvedUrl);
    if (!res.ok) {
      throw new Error(`Failed to fetch atlas: ${this.atlasUrl} -> ${resolvedUrl} (${res.status})`);
    }

    const data = (await res.json()) as CoreAtlas;
    this.atlas = data;
    this.resolvedAtlasBaseUrl = getDirectoryUrl(resolvedUrl);
    this.jsonLoaded = true;
    return data;
  }

  /**
   * 创建占位图（图集 PNG 缺失时使用，避免白屏）
   */
  private async createPlaceholderSource(size = 256): Promise<TextureSource> {
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d")!;
    // 渐变圆形占位符
    const grad = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    grad.addColorStop(0, "#a78bfa");
    grad.addColorStop(0.7, "#7c3aed");
    grad.addColorStop(1, "#4c1d95");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.3)";
    ctx.lineWidth = 2;
    ctx.stroke();
    const bitmap = await createImageBitmap(canvas);
    return new ImageSource({ resource: bitmap });
  }

  /**
   * 加载单张图片为 TextureSource（缺失时使用占位图）
   */
  private async loadImageSource(
    imagePath: string,
    baseUrl: string,
    cache: Map<string, TextureSource>
  ): Promise<TextureSource> {
    const cached = cache.get(imagePath);
    if (cached) return cached;

    // 图片路径相对于图集 JSON 所在目录解析（meta.images 中的路径）
    const fullUrl =
      imagePath.startsWith("http") || imagePath.startsWith("//")
        ? resolveUrl(imagePath)
        : new URL(imagePath, baseUrl).href;

    try {
      const res = await fetch(fullUrl);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const bitmap = await createImageBitmap(blob);
      const source = new ImageSource({ resource: bitmap });
      source.style.scaleMode = "linear"; // 线性滤波，缩放时边缘更平滑
      cache.set(imagePath, source);
      return source;
    } catch (err) {
      console.warn(
        `[TextureAtlas] 图集图片加载失败 "${imagePath}" (${fullUrl})，使用占位符。`,
        err
      );
      const placeholder = await this.createPlaceholderSource(256);
      placeholder.style.scaleMode = "linear";
      cache.set(imagePath, placeholder);
      return placeholder;
    }
  }

  /**
   * 加载所有图集图片（支持 meta.images 多图集）
   */
  private async loadAllImageSources(
    atlasData: CoreAtlas,
    atlasBaseUrl: string,
    cache: Map<string, TextureSource>
  ): Promise<TextureSource[]> {
    const meta = atlasData.meta;
    const imagePaths: string[] = meta.images?.length
      ? meta.images
      : meta.image
        ? [meta.image]
        : [];

    if (imagePaths.length === 0) {
      throw new Error("Atlas meta has no images");
    }

    const sources: TextureSource[] = [];
    for (const path of imagePaths) {
      const source = await this.loadImageSource(path, atlasBaseUrl, cache);
      sources.push(source);
    }
    return sources;
  }

  /** 加载法线图集 JSON */
  private async loadNormalAtlasJson(): Promise<CoreAtlas | null> {
    if (!this.normalAtlasUrl) return null;
    if (this.normalJsonLoaded && this.normalAtlas) return this.normalAtlas;

    const resolvedUrl = resolveUrl(this.normalAtlasUrl);
    const res = await fetch(resolvedUrl);
    if (!res.ok) return null;

    const data = (await res.json()) as CoreAtlas;
    this.normalAtlas = data;
    this.resolvedNormalAtlasBaseUrl = getDirectoryUrl(resolvedUrl);
    this.normalJsonLoaded = true;
    return data;
  }

  /**
   * 从 FrameData 创建 PIXI Texture
   */
  private createTextureFromFrame(
    source: TextureSource,
    frameData: FrameData
  ): Texture {
    const { frame: fr, rotated, trimmed, spriteSourceSize, sourceSize } = frameData;

    // 旋转时 frame 的 w/h 可能已交换（TexturePacker 格式）
    const frameRect = new Rectangle(fr.x, fr.y, fr.w, fr.h);

    const origRect = new Rectangle(0, 0, sourceSize.w, sourceSize.h);

    let trimRect: Rectangle | undefined;
    let defaultAnchor: { x: number; y: number } | undefined;

    if (trimmed && spriteSourceSize) {
      trimRect = new Rectangle(
        spriteSourceSize.x,
        spriteSourceSize.y,
        spriteSourceSize.w,
        spriteSourceSize.h
      );
      // 使用中心锚点，确保精灵居中显示且图集像素内容正确可见
      const sw = sourceSize.w;
      const sh = sourceSize.h;
      const ss = spriteSourceSize;
      defaultAnchor = {
        x: (ss.x + ss.w / 2) / sw,
        y: (ss.y + ss.h / 2) / sh,
      };
    }

    // groupD8: 0=无旋转, 2=90° CW
    const rotate = rotated ? 2 : 0;

    return new Texture({
      source,
      frame: frameRect,
      orig: origRect,
      trim: trimRect,
      defaultAnchor,
      rotate,
    });
  }

  /**
   * 根据 FrameData 获取对应的 TextureSource 索引
   */
  private getAtlasIndex(frameData: FrameData): number {
    return frameData.atlasIndex ?? 0;
  }

  /**
   * LRU：卸载最少最近使用的动画
   */
  private _unloadLeastRecentlyUsed(): void {
    if (this.animationCache.size <= this.maxCacheSize) return;

    let oldest: { key: string; entry: CacheEntry } | null = null;

    for (const [key, entry] of this.animationCache) {
      if (
        !oldest ||
        entry.lastAccessTime < oldest.entry.lastAccessTime ||
        (entry.lastAccessTime === oldest.entry.lastAccessTime &&
          entry.priority < oldest.entry.priority)
      ) {
        oldest = { key, entry };
      }
    }

    if (oldest) {
      for (const tex of oldest.entry.textures) {
        tex.destroy(false);
      }
      for (const tex of oldest.entry.normalTextures ?? []) {
        tex.destroy(false);
      }
      this.animationCache.delete(oldest.key);
    }
  }

  /**
   * 加载指定动画，返回 PIXI.Texture[]
   * 支持 meta.images 多图集合并
   */
  async loadAnimation(name: string): Promise<LoadedAnimationMeta> {
    const atlas = await this.loadAtlasJson();
    const animDef = atlas.animations[name];

    if (!animDef) {
      throw new Error(`Animation "${name}" not found in atlas`);
    }

    // 检查缓存
    const cached = this.animationCache.get(name);
    if (cached) {
      cached.lastAccessTime = Date.now();
      const config = this.animationConfigs.get(name) ?? {};
      return {
        textures: cached.textures,
        normalTextures: cached.normalTextures,
        fps: animDef.fps ?? config.fps ?? atlas.meta.fps,
        loop: animDef.loop ?? config.loop,
      };
    }

    // 确保缓存不超过上限
    this._unloadLeastRecentlyUsed();

    const baseUrl = this.resolvedAtlasBaseUrl ?? getDirectoryUrl(resolveUrl(this.atlasUrl));
    const sources = await this.loadAllImageSources(atlas, baseUrl, this.imageSources);

    // 兼容两种格式：A) frames 为帧名数组 + 顶层 atlas.frames  B) frames 为内嵌帧数据对象
    let frameEntries: [string, FrameData][];
    if (isAnimationDefArray(animDef)) {
      const frames = atlas.frames ?? {};
      frameEntries = animDef.frames
        .map((name) => [name, frames[name]] as [string, FrameData])
        .filter(([, data]) => data != null);
    } else {
      const framesObj = animDef.frames as Record<string, FrameData>;
      frameEntries = Object.keys(framesObj)
        .sort()
        .map((name) => [name, framesObj[name]] as [string, FrameData]);
    }

    const textures: Texture[] = [];
    for (const [frameName, frameData] of frameEntries) {
      const atlasIndex = this.getAtlasIndex(frameData);
      const source = sources[atlasIndex];
      if (!source) {
        console.warn(
          `Atlas index ${atlasIndex} out of range for frame "${frameName}"`
        );
        continue;
      }
      const texture = this.createTextureFromFrame(source, frameData);
      textures.push(texture);
    }

    // 加载法线贴图（若配置了 normalAtlasUrl）
    let normalTextures: Texture[] | undefined;
    const normalAtlasData = await this.loadNormalAtlasJson();
    if (normalAtlasData) {
      const normalAnim = normalAtlasData.animations[name];
      if (normalAnim) {
        const normalBaseUrl =
          this.resolvedNormalAtlasBaseUrl ?? getDirectoryUrl(resolveUrl(this.normalAtlasUrl!));
        const normalSources = await this.loadAllImageSources(
          normalAtlasData,
          normalBaseUrl,
          this.normalImageSources
        );
        const normalFrameEntries = isAnimationDefArray(normalAnim)
          ? (normalAnim.frames
              .map((n) => [n, (normalAtlasData.frames ?? {})[n]] as [string, FrameData])
              .filter(([, d]) => d != null) as [string, FrameData][])
          : (Object.keys(normalAnim.frames as Record<string, FrameData>)
              .sort()
              .map((n) => [n, (normalAnim.frames as Record<string, FrameData>)[n]] as [string, FrameData]) as [string, FrameData][]);
        normalTextures = [];
        for (const [frameName, frameData] of normalFrameEntries) {
          const idx = frameData.atlasIndex ?? 0;
          const src = normalSources[idx];
          if (src) {
            normalTextures.push(this.createTextureFromFrame(src, frameData));
          }
        }
      }
    }

    const config = this.animationConfigs.get(name) ?? {};
    const priority = config.priority ?? this.defaultPriority;

    this.animationCache.set(name, {
      textures,
      normalTextures,
      lastAccessTime: Date.now(),
      priority,
    });

    return {
      textures,
      normalTextures,
      fps: animDef.fps ?? config.fps ?? atlas.meta.fps,
      loop: animDef.loop ?? config.loop,
    };
  }

  /**
   * 设置动画配置（LRU 优先级、循环等）
   */
  setAnimationConfig(name: string, config: AnimationConfig): void {
    this.animationConfigs.set(name, config);
  }

  /**
   * 获取图集中所有动画名称
   */
  async getAnimationNames(): Promise<string[]> {
    const atlas = await this.loadAtlasJson();
    return Object.keys(atlas.animations);
  }

  /**
   * 预加载指定动画（可选）
   */
  async preloadAnimations(names: string[]): Promise<void> {
    await Promise.all(names.map((n) => this.loadAnimation(n)));
  }

  /**
   * 销毁管理器，释放所有资源
   */
  destroy(): void {
    for (const entry of this.animationCache.values()) {
      for (const tex of entry.textures) {
        tex.destroy(false);
      }
      for (const tex of entry.normalTextures ?? []) {
        tex.destroy(false);
      }
    }
    this.animationCache.clear();

    for (const source of this.imageSources.values()) {
      source.destroy?.();
    }
    this.imageSources.clear();
    for (const source of this.normalImageSources.values()) {
      source.destroy?.();
    }
    this.normalImageSources.clear();

    this.atlas = null;
    this.normalAtlas = null;
    this.jsonLoaded = false;
    this.normalJsonLoaded = false;
  }
}
