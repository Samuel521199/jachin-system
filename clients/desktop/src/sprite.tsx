/**
 * Sprite Window - 桌面精灵主窗口
 *
 * 仅渲染宠物本体，无 InputBar。聊天通过右键菜单「打开聊天」进入 chat 窗口。
 */

import React from "react";
import ReactDOM from "react-dom/client";
import { SpriteWindowContent } from "./components/Sprite/SpriteWindowContent";
import "./styles/globals.css";

const root = document.getElementById("sprite-root");
if (root) {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <SpriteWindowContent />
    </React.StrictMode>
  );
}
