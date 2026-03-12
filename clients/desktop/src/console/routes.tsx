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
import { RecruitmentDashboard } from "./pages/RecruitmentDashboard";

export const consoleRoutes = [
  {
    path: "/",
    element: <ConsoleLayout />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: <Dashboard /> },
      { path: "brain", element: <NeuralNexus /> },
      { path: "calendar", element: <Calendar /> },
      { path: "skills", element: <SkillMatrix /> },
      { path: "recruitment", element: <RecruitmentDashboard /> },
      { path: "network", element: <JachinLink /> },
      { path: "wake", element: <WakeModePanel /> },
      { path: "settings", element: <Persona /> },
      { path: "preferences", element: <SettingsPanel /> },
    ],
  },
];

export const consoleRouter = createHashRouter(consoleRoutes);
