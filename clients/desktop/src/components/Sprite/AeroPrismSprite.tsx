/**
 * AeroPrismSprite - Aero Prism 风格的桌面精灵主组件
 * 
 * 分离式架构：
 * - AvatarContainer: 精灵形象
 * - InputBar: 输入胶囊
 * - FloatingMenu: 右键菜单
 * 
 * 功能：
 * - 双模拖拽（Avatar 和 InputBar 独立拖拽）
 * - 磁吸效果（InputBar 靠近 Avatar 时自动对齐）
 * - 多模态输入（文本+语音）
 */

import React, { useState, useRef, useEffect } from "react";
import { motion, useMotionValue } from "framer-motion";
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { listen } from "@tauri-apps/api/event";
import { AvatarContainer } from "./AvatarContainer";
import { PetScreenWithLayout } from "../PetScreen";
import { InputBar } from "../Input/InputBar";
import { FloatingMenu, defaultMenuActions } from "../Menu/FloatingMenu";
import { SDUICard } from "../SDRenderer/SDUICard";
import { useSpriteStore } from "../../store/spriteStore";
import { sendChatMessage, voiceChat, invokePlugin } from "../../lib/api";
import "../../styles/globals.css";

const MAGNETIC_THRESHOLD = 80; // 磁吸阈值（像素）

export const AeroPrismSprite: React.FC = () => {
  const { state, setState, avatarId } = useSpriteStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ x: 0, y: 0 });
  const [isInputSnapped, setIsInputSnapped] = useState(false);
  const [sduiSchema, setSduiSchema] = useState<string | null>(null);
  const [sduiCardOpen, setSduiCardOpen] = useState(false);
  const [sduiError, setSduiError] = useState<{
    message: string;
    code?: string;
    traceId?: string;
    retryable?: boolean;
  } | null>(null);
  
  const avatarRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  
  // Framer Motion 位置值
  const inputX = useMotionValue(0);
  const inputY = useMotionValue(0);

  // 监听状态变化
  useEffect(() => {
    let unlistenFn: (() => void) | null = null;

    listen("sprite-state-change", (event: any) => {
      const newState = event.payload?.state;
      if (newState && ["idle", "listening", "thinking", "speaking"].includes(newState)) {
        setState(newState as any);
      }
    }).then((fn) => {
      unlistenFn = fn;
    });

    return () => {
      if (unlistenFn) {
        unlistenFn();
      }
    };
  }, [setState]);

  // 磁吸效果检测
  useEffect(() => {
    if (!avatarRef.current || !inputRef.current || !containerRef.current) return;

    const checkMagneticSnap = () => {
      const avatarRect = avatarRef.current!.getBoundingClientRect();
      const inputRect = inputRef.current!.getBoundingClientRect();
      const containerRect = containerRef.current!.getBoundingClientRect();

      // Avatar 底部中心点（世界坐标）
      const avatarBottomCenter = {
        x: avatarRect.left + avatarRect.width / 2,
        y: avatarRect.top + avatarRect.height,
      };

      // InputBar 顶部中心点（世界坐标）
      const inputTopCenter = {
        x: inputRect.left + inputRect.width / 2,
        y: inputRect.top,
      };

      // 计算距离
      const distance = Math.sqrt(
        Math.pow(avatarBottomCenter.x - inputTopCenter.x, 2) +
        Math.pow(avatarBottomCenter.y - inputTopCenter.y, 2)
      );

      // 如果距离小于阈值，触发磁吸
      if (distance < MAGNETIC_THRESHOLD && !isInputSnapped) {
        setIsInputSnapped(true);
        
        // 计算目标位置（相对于容器）
        // InputBar 应该对齐到 Avatar 底部中心
        const targetX = avatarBottomCenter.x - containerRect.left - inputRect.width / 2;
        const targetY = avatarBottomCenter.y - containerRect.top + 10; // 10px 间距

        // 使用 Framer Motion 动画到目标位置
        inputX.set(targetX);
        inputY.set(targetY);
      } else if (distance > MAGNETIC_THRESHOLD * 1.5) {
        setIsInputSnapped(false);
      }
    };

    const interval = setInterval(checkMagneticSnap, 100);
    return () => clearInterval(interval);
  }, [isInputSnapped, inputX, inputY]);

  // 右键菜单处理
  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    setMenuPosition({ x: e.clientX, y: e.clientY });
    setMenuOpen(true);
  };

  // 双击处理
  const handleDoubleClick = async () => {
    try {
      const chatWindow = await WebviewWindow.getByLabel("chat");
      if (chatWindow) {
        const spritePos = await getCurrentWindow().innerPosition();
        await chatWindow.setPosition(new PhysicalPosition(spritePos.x + 140, spritePos.y));
        await chatWindow.show();
        await chatWindow.setFocus();
      }
    } catch (error) {
      console.error("Failed to open chat window:", error);
    }
  };

  // 发送消息
  const handleSend = async (message: string) => {
    setState("thinking");
    setSduiCardOpen(false);
    setSduiSchema(null);
    setSduiError(null);
    
    try {
      // 使用自然语言查询，让 LLM 自动匹配插件和方法
      // 支持多种表达方式，如 "查看电脑状态"、"电脑好卡"、"检查性能" 等
      if (message.includes("查看") || message.includes("检查") || message.includes("状态") || 
          message.includes("性能") || message.includes("卡") || message.includes("慢")) {
        // 使用自然语言查询，IntentPlanner 会自动匹配到合适的插件
        const pluginResponse = await invokePlugin(message);
        
        if (pluginResponse.status_code === 200 && pluginResponse.ui_render_schema) {
          setSduiSchema(pluginResponse.ui_render_schema);
          setSduiCardOpen(true);
          setState("idle");
        } else {
          // 处理错误
          setSduiError({
            message: pluginResponse.error_message || "插件调用失败",
            code: `HTTP_${pluginResponse.status_code}`,
            traceId: pluginResponse.trace_id,
            retryable: pluginResponse.status_code === 503 || pluginResponse.status_code === 504,
          });
          setSduiCardOpen(true);
          setState("idle");
        }
        return;
      }
      
      // 普通聊天消息
      const response = await sendChatMessage(message);
      setState("speaking");
      
      // 使用打字机效果（如果需要显示在聊天窗口）
      setTimeout(() => setState("idle"), 3000);
    } catch (error) {
      console.error("Failed to send message:", error);
      // 显示错误
      setSduiError({
        message: error instanceof Error ? error.message : "未知错误",
        code: "CLIENT_ERROR",
        retryable: true,
      });
      setSduiCardOpen(true);
      setState("idle");
    }
  };
  
  // 重试处理
  const handleRetry = () => {
    // 获取最后一条消息并重试
    // 这里简化处理，实际应该保存最后的消息
    setSduiError(null);
    setSduiCardOpen(false);
  };

  // 表单提交处理
  const handleFormSubmit = async (data: any) => {
    console.log("Form submitted:", data);
    setState("thinking");
    
    try {
      // 如果有 action_id，可以调用对应的插件方法
      if (data.action_id) {
        const pluginResponse = await invokePlugin(
          data.plugin_id || "com.jachin.sys-monitor",
          data.action_id,
          data
        );
        
        if (pluginResponse.status_code === 200 && pluginResponse.ui_render_schema) {
          setSduiSchema(pluginResponse.ui_render_schema);
          setSduiCardOpen(true);
          setState("idle");
        } else {
          setSduiError({
            message: pluginResponse.error_message || "操作失败",
            code: `HTTP_${pluginResponse.status_code}`,
            traceId: pluginResponse.trace_id,
            retryable: pluginResponse.status_code === 503 || pluginResponse.status_code === 504,
          });
          setSduiCardOpen(true);
          setState("idle");
        }
      } else {
        // 默认处理：显示成功消息
        setState("idle");
        setSduiCardOpen(false);
        // TODO: 可以显示一个成功提示
      }
    } catch (error) {
      console.error("Failed to submit form:", error);
      setSduiError({
        message: error instanceof Error ? error.message : "提交失败",
        code: "CLIENT_ERROR",
        retryable: true,
      });
      setSduiCardOpen(true);
      setState("idle");
    }
  };

  // 语音开始
  const handleVoiceStart = () => {
    setState("listening");
  };

  // 语音结束
  const handleVoiceEnd = async (audioBlob: Blob) => {
    setState("thinking");
    try {
      const audioFile = new File([audioBlob], "recording.wav", { type: "audio/wav" });
      const response = await voiceChat(audioFile, "wav", "zh-CN", true, "zh-CN-XiaoxiaoNeural");
      
      setState("speaking");
      setTimeout(() => setState("idle"), 3000);
    } catch (error) {
      console.error("Failed to process voice:", error);
      setState("idle");
    }
  };

  // Pixi 图集精灵模式：使用 PetScreenWithLayout（含 InputBar + FloatingMenu）
  if (avatarId === "pixi") {
    return <PetScreenWithLayout />;
  }

  return (
    <div
      ref={containerRef}
      className="w-full h-full bg-transparent overflow-hidden"
      style={{ userSelect: "none" }}
    >
      {/* Avatar Container */}
      <div
        ref={avatarRef}
        className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2"
      >
        <AvatarContainer
          onContextMenu={handleContextMenu}
          onDoubleClick={handleDoubleClick}
        />
      </div>

      {/* Input Bar - 可独立拖拽 */}
      <motion.div
        ref={inputRef}
        className="absolute bottom-8 left-1/2"
        drag
        dragMomentum={false}
        dragConstraints={containerRef}
        dragElastic={0.1}
        style={{
          x: inputX,
          y: inputY,
        }}
        animate={
          isInputSnapped
            ? {
                x: inputX.get(),
                y: inputY.get(),
                transition: { type: "spring", stiffness: 300, damping: 30 },
              }
            : {}
        }
        onDragStart={() => {
          // 开始拖拽时取消磁吸
          setIsInputSnapped(false);
        }}
        onDragEnd={() => {
          // 拖拽结束后重新检查磁吸
          // 磁吸检查会在 useEffect 中自动进行
        }}
      >
        {/* SDUI Card - 显示在 InputBar 上方 */}
        <SDUICard
          isOpen={sduiCardOpen}
          sduiSchema={sduiSchema}
          error={sduiError}
          onClose={() => {
            setSduiCardOpen(false);
            setSduiSchema(null);
            setSduiError(null);
          }}
          onRetry={handleRetry}
          onSubmit={handleFormSubmit}
        />
        
        <InputBar
          onSend={handleSend}
          onVoiceStart={handleVoiceStart}
          onVoiceEnd={handleVoiceEnd}
          disabled={state === "thinking"}
        />
      </motion.div>

      {/* Floating Menu */}
      <FloatingMenu
        isOpen={menuOpen}
        position={menuPosition}
        actions={defaultMenuActions}
        onClose={() => setMenuOpen(false)}
      />
    </div>
  );
};
