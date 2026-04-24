/**
 * 控制台路由定义
 */

import { createHashRouter, Navigate } from "react-router-dom";
import { ConsoleLayout } from "./ConsoleLayout";
import { Dashboard } from "./pages/Dashboard";
import { NeuralNexus } from "./pages/NeuralNexus";
import { Calendar } from "./pages/Calendar";
import { SkillMatrix } from "./pages/SkillMatrix";
import { JachinLink } from "./pages/JachinLink";
import { Persona } from "./pages/Persona";
import { SettingsPanel } from "./pages/SettingsPanel";
import { WakeModePanel } from "./pages/WakeModePanel";
import { SafetyLockApproval } from "./pages/SafetyLockApproval";
import { MonitorMatrix } from "./pages/MonitorMatrix";
import { K11UnifiedSmokeTest } from "./pages/K11UnifiedSmokeTest";

export const consoleRoutes = [
  {
    path: "/",
    element: <ConsoleLayout />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: <Dashboard /> },
      { path: "brain", element: <NeuralNexus /> },
      { path: "safety-lock", element: <SafetyLockApproval /> },
      { path: "calendar", element: <Calendar /> },
      { path: "skills", element: <SkillMatrix /> },
      { path: "monitor", element: <MonitorMatrix /> },
      { path: "k11-smoke", element: <K11UnifiedSmokeTest /> },
      { path: "network", element: <JachinLink /> },
      { path: "wake", element: <WakeModePanel /> },
      { path: "settings", element: <Persona /> },
      { path: "preferences", element: <SettingsPanel /> },
    ],
  },
];

export const consoleRouter = createHashRouter(consoleRoutes);
