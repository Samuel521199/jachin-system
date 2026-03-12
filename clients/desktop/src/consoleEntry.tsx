/**
 * 控制台窗口专用入口
 * main 窗口加载 console.html 时直接挂载 ConsoleApp，不依赖 getCurrentWindow().label()
 */

import React from "react";
import ReactDOM from "react-dom/client";
import { ConsoleApp } from "./console/ConsoleApp";
import "./styles/globals.css";

const root = document.getElementById("console-root");
if (root) {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <ConsoleApp />
    </React.StrictMode>
  );
}
