/**
 * PetScreen - 桌面宠物 Pixi 渲染主屏
 *
 * 集成 PixiSpriteRenderer，实现：
 * - 默认 Idle，点击触发 Smile 一次后回 Idle
 * - 空闲 N 分钟无操作自动 SLEEP（可配置，默认 15 分钟）
 * - 拖拉窗口时显示 PICKED
 * - 透明背景（Tauri transparent: true）
 */

import React, { useEffect, useRef, useState, useCallback, forwardRef, useImperativeHandle } from "react";
import { TextureAtlasManager, PixiSpriteRenderer } from "@/systems/animation/driver";
import { useSpriteStore } from "@/store/spriteStore";
import { getAvatarConfig } from "@/config/avatars";
import type { PetAction } from "@/store/spriteStore";

export interface PetScreenProps {
  /** 画布尺寸（默认与容器一致） */
  width?: number;
  height?: number;
  /** 右键菜单回调 */
  onContextMenu?: (e: React.MouseEvent) => void;
  /** 双击回调（如打开聊天窗口） */
  onDoubleClick?: () => void;
}

export interface PetScreenHandle {
  playPicked: () => void;
}

export const PetScreen = forwardRef<PetScreenHandle, PetScreenProps>(({
  width,
  height,
  onContextMenu,
  onDoubleClick,
}, ref) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<PixiSpriteRenderer | null>(null);
  const atlasManagerRef = useRef<TextureAtlasManager | null>(null);

  const { getAtlasAnimation, useNormalMap, pixiAvatarId, pixiScale, idleToSleepSeconds } =
    useSpriteStore();
  const [animationState, setAnimationState] = useState<PetAction>("idle");
  const [isReady, setIsReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** 空闲计时器 */
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** 重置空闲计时器 */
  const resetIdleTimer = useCallback(() => {
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    const sec = Math.max(10, idleToSleepSeconds);
    idleTimerRef.current = setTimeout(() => {
      setAnimationState("sleep");
      idleTimerRef.current = null;
    }, sec * 1000);
  }, [idleToSleepSeconds]);

  /** 播放动画并重置空闲计时 */
  const playAnimation = useCallback(
    async (action: PetAction, force = false) => {
      const renderer = rendererRef.current;
      if (!renderer) return;

      const atlasAnim = getAtlasAnimation(action);
      await renderer.play(atlasAnim, force, () => {
        // 非循环动画结束：smile、picked 播完后回 idle
        if (action === "touch" || action === "smile" || action === "picked") {
          setAnimationState("idle");
          resetIdleTimer();
        }
      });
      setAnimationState(action);
      if (action !== "sleep") {
        resetIdleTimer();
      }
    },
    [resetIdleTimer, getAtlasAnimation]
  );

  /** 精灵被点击（pointertap，非拖拉）：触发 smile */
  const handleSpriteClick = useCallback(() => {
    playAnimation("smile", true);
  }, [playAnimation]);

  /** 暴露给父组件：拖动时播放 PICKED（由 DraggableRegion 的 onDragStart 调用） */
  useImperativeHandle(ref, () => ({
    playPicked: () => playAnimation("picked", true),
  }), [playAnimation]);

  /** 动画状态变化时播放（通过 mapping 转为图集动画名） */
  useEffect(() => {
    if (!isReady || !rendererRef.current) return;
    void playAnimation(animationState);
  }, [animationState, isReady, playAnimation]);

  /** 初始化 Pixi 渲染器 */
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const avatarConfig = getAvatarConfig(pixiAvatarId);
    if (!avatarConfig) {
      setError(`形象 "${pixiAvatarId}" 未找到`);
      return;
    }

    const atlasManager = new TextureAtlasManager({
      atlasUrl: avatarConfig.colorAtlasUrl,
      normalAtlasUrl: avatarConfig.normalAtlasUrl,
      maxCacheSize: 8,
    });
    atlasManagerRef.current = atlasManager;

    const renderer = new PixiSpriteRenderer({
      canvas,
      atlasManager,
      defaultFps: 24,
      useNormalMap,
      appOptions: {
        backgroundAlpha: 0,
        backgroundColor: 0x000000,
        antialias: true, // 启用 MSAA 抗锯齿，减轻边缘锯齿感
      },
    });
    rendererRef.current = renderer;

    const init = async () => {
      try {
        // 先等待布局完成，确保容器有有效尺寸（Pixi resizeTo 依赖画布尺寸，0 尺寸会导致不渲染）
        const ensureSize = () => {
          const rect = container.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) return rect;
          return null;
        };
        let rect = ensureSize();
        if (!rect) {
          await new Promise<void>((r) => requestAnimationFrame(() => r()));
          rect = ensureSize();
        }
        if (!rect) {
          await new Promise((r) => setTimeout(r, 100));
          rect = ensureSize();
        }

        const w = Math.max(1, width ?? rect?.width ?? 400);
        const h = Math.max(1, height ?? rect?.height ?? 300);

        // 显式设置画布尺寸，确保 Pixi 初始化时 canvas 有有效 clientWidth/clientHeight
        canvas.width = w;
        canvas.height = h;

        await renderer.init();
        renderer.resize(); // 确保 Pixi 视口与 canvas 尺寸同步
        renderer.setOnSpriteClick(handleSpriteClick);

        // 精灵居中，scale=1 为像素级（frameSize 256 即 256px），pixiScale>1 为故意放大
        renderer.setPosition(w / 2, h / 2);
        renderer.setScale(Math.max(0.1, pixiScale));

        setIsReady(true);
        resetIdleTimer();
      } catch (err) {
        const msg = err instanceof Error ? err.message : "初始化失败";
        setError(msg);
        console.error("[PetScreen] Init error:", err);
      }
    };

    void init();

    return () => {
      if (idleTimerRef.current) {
        clearTimeout(idleTimerRef.current);
      }
      renderer.destroy();
      atlasManager.destroy();
      rendererRef.current = null;
      atlasManagerRef.current = null;
      setIsReady(false);
    };
  }, [handleSpriteClick, resetIdleTimer, width, height, useNormalMap, pixiAvatarId, pixiScale]);

  /** 容器尺寸变化时更新画布、Pixi 视口和精灵位置 */
  useEffect(() => {
    if (!isReady || !containerRef.current || !rendererRef.current || !canvasRef.current) return;

    const container = containerRef.current;
    const canvas = canvasRef.current;
    const observer = new ResizeObserver(() => {
      const rect = container.getBoundingClientRect();
      const w = Math.max(1, width ?? rect.width);
      const h = Math.max(1, height ?? rect.height);
      canvas.width = w;
      canvas.height = h;
      rendererRef.current?.resize();
      rendererRef.current?.setPosition(w / 2, h / 2);
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [isReady, width, height]);

  /** pixiScale 变化时更新精灵缩放 */
  useEffect(() => {
    if (!isReady || !rendererRef.current) return;
    rendererRef.current.setScale(Math.max(0.1, pixiScale));
  }, [isReady, pixiScale]);

  /** 全局交互（点击、移动）重置空闲计时 */
  useEffect(() => {
    if (!isReady) return;

    const handleActivity = () => {
      if (animationState === "sleep") {
        setAnimationState("idle");
      }
      resetIdleTimer();
    };

    window.addEventListener("mousedown", handleActivity);
    window.addEventListener("mousemove", handleActivity);
    window.addEventListener("keydown", handleActivity);
    return () => {
      window.removeEventListener("mousedown", handleActivity);
      window.removeEventListener("mousemove", handleActivity);
      window.removeEventListener("keydown", handleActivity);
    };
  }, [isReady, animationState, resetIdleTimer]);

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    onContextMenu?.(e);
  };

  const handleDoubleClick = () => {
    onDoubleClick?.();
  };

  if (error) {
    return (
      <div
        ref={containerRef}
        className="w-full h-full flex items-center justify-center bg-transparent text-red-400 text-sm"
      >
        {error}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="w-full h-full relative overflow-hidden bg-transparent"
      style={{ userSelect: "none" }}
      onContextMenu={handleContextMenu}
      onDoubleClick={handleDoubleClick}
    >
      <canvas
        ref={canvasRef}
        className="block w-full h-full"
        style={{
          width: width ? `${width}px` : "100%",
          height: height ? `${height}px` : "100%",
          display: "block",
        }}
      />
    </div>
  );
});
