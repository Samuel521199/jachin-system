import React from "react";
import ReactDOM from "react-dom/client";
import { NotificationApp } from "./components/JachinSentry/NotificationApp";
import "./styles/globals.css";

ReactDOM.createRoot(document.getElementById("notification-root")!).render(
  <React.StrictMode>
    <NotificationApp />
  </React.StrictMode>,
);
