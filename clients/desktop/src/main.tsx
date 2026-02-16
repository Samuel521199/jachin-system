/**
 * Main Entry - 根据窗口标签路由到不同组件
 * 
 * 这个文件用于传统的控制台模式（如果保留）
 * 精灵模式使用 sprite.tsx 和 chat.tsx
 */

import React from "react";
import ReactDOM from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";
import App from "./App";
import { ConsoleApp } from "./console/ConsoleApp";
import "./styles/globals.css";

// 根据窗口标签路由（Tauri v2: label 是同步属性）
let label = "main";
try {
  label = getCurrentWindow().label;
} catch {
  // 非 Tauri 环境（如浏览器预览）时回退
}
(function routeByLabel() {
    const root = document.getElementById("root");
    if (!root) return;

    if (label === "sprite") {
      // 精灵窗口 - 加载 sprite.tsx（已在 sprite.html 中处理）
      console.log("Sprite window loaded");
    } else if (label === "chat") {
      // 对话窗口 - 加载 chat.tsx（已在 chat.html 中处理）
      console.log("Chat window loaded");
    } else if (label === "main") {
      // 控制台窗口 - 使用 ConsoleLayout + 路由（Dashboard / Brain / Skills / Network / Settings）
      ReactDOM.createRoot(root).render(
        <React.StrictMode>
          <ConsoleApp />
        </React.StrictMode>
      );
    } else {
      // 默认窗口 - 加载传统控制台界面（设备/聊天/技能等）
      ReactDOM.createRoot(root).render(
        <React.StrictMode>
          <App />
        </React.StrictMode>
      );
    }
})();
