/**
 * ConsoleApp - 控制台应用入口
 * 使用 HashRouter 适配 Tauri 单页与本地加载
 */

import { RouterProvider } from "react-router-dom";
import { consoleRouter } from "./routes";

export function ConsoleApp() {
  return <RouterProvider router={consoleRouter} />;
}
