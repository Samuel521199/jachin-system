/**
 * PixiSpriteRenderer - 基于 PixiJS 的动画精灵渲染器
 * 职责：管理 PIXI.Application、AnimatedSprite，play、位置锁定、onComplete
 */

import {
  Application,
  AnimatedSprite,
  Container,
  extensions,
  ResizePlugin,
  Ticker,
  type ApplicationOptions,
} from "pixi.js";

// 移除 ResizePlugin 避免 destroy 时 _cancelResize 报错；改用手动 width/height + resize
extensions.remove(ResizePlugin);
import type { TextureAtlasManager } from "./TextureAtlasManager";

/** 渲染器配置 */
export interface PixiSpriteRendererOptions {
  /** 画布元素 */
  canvas: HTMLCanvasElement;
  /** 纹理图集管理器 */
  atlasManager: TextureAtlasManager;
  /** PIXI Application 初始化选项（可选） */
  appOptions?: Partial<ApplicationOptions>;
  /** 默认动画帧率 */
  defaultFps?: number;
  /** 是否启用法线贴图（需 TextureAtlasManager 提供 normalTextures） */
  useNormalMap?: boolean;
}

/** 播放完成回调 */
export type OnCompleteCallback = () => void;

/**
 * PixiJS 动画精灵渲染器
 * - 使用 Ticker 驱动动画
 * - 位置锁定（updateAnchor）防止 Trim 抖动
 * - 播放结束时触发 onComplete
 */
export class PixiSpriteRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly atlasManager: TextureAtlasManager;
  private readonly defaultFps: number;
  private readonly useNormalMap: boolean;
  private readonly appOptions?: Partial<ApplicationOptions>;

  private app: Application | null = null;
  private stage: Container | null = null;
  private sprite: AnimatedSprite | null = null;

  /** 待应用的位置（精灵创建前调用 setPosition 时存储） */
  private pendingPosition: { x: number; y: number } = { x: 0, y: 0 };
  /** 待应用的缩放（精灵创建前调用 setScale 时存储） */
  private pendingScale = 1;

  /** 当前播放的动画名 */
  private currentAnimationName: string | null = null;

  /** 播放完成回调 */
  private onCompleteCallback: OnCompleteCallback | null = null;

  /** 精灵点击回调（用于 HitArea 交互） */
  private onSpriteClickCallback: (() => void) | null = null;

  constructor(options: PixiSpriteRendererOptions) {
    this.canvas = options.canvas;
    this.atlasManager = options.atlasManager;
    this.defaultFps = options.defaultFps ?? 24;
    this.useNormalMap = options.useNormalMap ?? false;
    this.appOptions = options.appOptions;
  }

  /**
   * 初始化 PIXI Application
   * 注：未使用 ResizePlugin，用手动 width/height + resize() 避免 destroy 报错
   */
  async init(): Promise<void> {
    if (this.app) return;

    const w = Math.max(1, this.canvas.width || this.canvas.clientWidth || 400);
    const h = Math.max(1, this.canvas.height || this.canvas.clientHeight || 300);
    const resolution = window.devicePixelRatio ?? 1;

    this.app = new Application();
    await this.app.init({
      canvas: this.canvas,
      width: w,
      height: h,
      autoDensity: true,
      resolution,
      ...this.appOptions,
    });

    // 若在 await 期间已 destroy，app 已被置空，直接返回
    if (!this.app) return;
    this.stage = this.app.stage;
  }

  /**
   * 获取 Ticker（用于外部驱动或手动更新）
   */
  get ticker(): Ticker | null {
    return this.app?.ticker ?? null;
  }

  /**
   * 手动触发视口重算（容器尺寸变化时调用，未使用 ResizePlugin 故直接调 renderer.resize）
   */
  resize(): void {
    if (!this.app?.renderer) return;
    const w = Math.max(1, this.canvas.width || this.canvas.clientWidth);
    const h = Math.max(1, this.canvas.height || this.canvas.clientHeight);
    this.app.renderer.resize(w, h);
  }

  /**
   * 播放指定动画
   * @param name 动画名（如 IDLE, TOUCH, SLEEP）
   * @param force 是否强制重新播放（即使当前已在播放同名动画）
   * @param onComplete 播放完成回调（非循环动画）
   */
  async play(
    name: string,
    force = false,
    onComplete?: OnCompleteCallback
  ): Promise<void> {
    if (!this.app || !this.stage) {
      await this.init();
    }

    if (!this.app || !this.stage) {
      throw new Error("PixiSpriteRenderer init failed");
    }

    const alreadyPlaying = this.currentAnimationName === name && this.sprite?.playing;
    if (!force && alreadyPlaying) {
      return;
    }

    this.onCompleteCallback = onComplete ?? null;

    const { textures, normalTextures, fps, loop } = await this.atlasManager.loadAnimation(name);
    if (textures.length === 0) {
      console.warn(`[PixiSpriteRenderer] Animation "${name}" has no frames`);
      return;
    }

    // smile、picked、touch 等播一次即停，不循环
    const oneShot = ["SMILE", "PICKED", "TOUCH"].includes(name.toUpperCase());
    const shouldLoop = oneShot ? false : (loop ?? true);

    if (this.sprite) {
      this.sprite.textures = textures;
      this.sprite.loop = shouldLoop;
      this.sprite.animationSpeed = (fps ?? this.defaultFps) / 60;
      this.sprite.gotoAndPlay(0);
    } else {
      this.sprite = new AnimatedSprite({
        textures,
        loop: shouldLoop,
        animationSpeed: (fps ?? this.defaultFps) / 60,
        autoPlay: true,
        autoUpdate: true,
        roundPixels: false, // 亚像素渲染，配合 antialias 减轻边缘锯齿
        // 位置锁定：每帧切换时使用 Texture 的 defaultAnchor，防止 Trim 导致抖动
        updateAnchor: true,
        onComplete: () => this._handleComplete(),
      });
      this.stage.addChild(this.sprite);
      // 应用初始化前设置的 position/scale（精灵在首次 play 时才创建）
      this.sprite.x = this.pendingPosition.x;
      this.sprite.y = this.pendingPosition.y;
      this.sprite.scale.set(this.pendingScale);
      this._setupSpriteInteraction();
    }

    // 法线贴图：normalTextures 已加载，可在此扩展 Filter 实现光照（TODO）
    if (this.useNormalMap && normalTextures?.length) {
      // 预留：sprite.filters = [createNormalMapFilter(normalTextures[currentFrame])]
    }

    this.currentAnimationName = name;
  }

  /**
   * 设置精灵点击回调（点击有像素区域时触发，透明区域不触发）
   * 需在首次 play 之后精灵才会存在
   */
  setOnSpriteClick(callback: (() => void) | null): void {
    this.onSpriteClickCallback = callback;
    this._setupSpriteInteraction();
  }

  /** 配置精灵的交互（eventMode、pointertap 区分点击与拖拉） */
  private _setupSpriteInteraction(): void {
    if (!this.sprite || !this.onSpriteClickCallback) return;

    this.sprite.eventMode = "static";
    this.sprite.cursor = "pointer";
    this.sprite.off("pointertap");
    this.sprite.on("pointertap", () => {
      this.onSpriteClickCallback?.();
    });
  }

  /**
   * 内部：播放完成处理
   */
  private _handleComplete(): void {
    const cb = this.onCompleteCallback;
    this.onCompleteCallback = null;
    cb?.();
  }

  /**
   * 停止当前动画
   */
  stop(): void {
    this.sprite?.stop();
  }

  /**
   * 设置精灵位置（精灵创建前会存储，创建时自动应用）
   */
  setPosition(x: number, y: number): void {
    this.pendingPosition = { x, y };
    if (this.sprite) {
      this.sprite.x = x;
      this.sprite.y = y;
    }
  }

  /**
   * 设置精灵缩放（精灵创建前会存储，创建时自动应用）
   */
  setScale(scale: number): void {
    this.pendingScale = scale;
    if (this.sprite) {
      this.sprite.scale.set(scale);
    }
  }

  /**
   * 获取当前 AnimatedSprite（只读）
   */
  getSprite(): AnimatedSprite | null {
    return this.sprite;
  }

  /**
   * 获取当前动画名
   */
  getCurrentAnimation(): string | null {
    return this.currentAnimationName;
  }

  /**
   * 是否正在播放
   */
  get playing(): boolean {
    return this.sprite?.playing ?? false;
  }

  /**
   * 销毁渲染器
   * 注：Pixi v8 ResizePlugin.destroy 存在 _cancelResize 未定义问题，用 try-catch 兜底
   */
  destroy(): void {
    if (this.sprite) {
      this.sprite.destroy();
      this.sprite = null;
    }
    this.stage = null;
    if (this.app) {
      try {
        this.app.destroy(true, { children: true });
      } catch (e) {
        console.warn("[PixiSpriteRenderer] App destroy error (Pixi v8 ResizePlugin):", e);
      }
      this.app = null;
    }
    this.currentAnimationName = null;
    this.onCompleteCallback = null;
  }
}
