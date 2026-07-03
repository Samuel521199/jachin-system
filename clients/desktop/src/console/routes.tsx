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
import { GameQAPanel } from "./pages/GameQAPanel";
import { ProjectManagement } from "./pages/ProjectManagement";
import { BIAnalysis } from "./pages/BIAnalysis";
import { OsEvidencePanel } from "./pages/OsEvidencePanel";
import { CapabilityPublish } from "./pages/CapabilityPublish";
import { CapabilityInstallCenter } from "./pages/CapabilityInstallCenter";
import { EnglishVocabPanel } from "./pages/EnglishVocabPanel";

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
      { path: "capability-publish", element: <CapabilityPublish /> },
      { path: "capability-install", element: <CapabilityInstallCenter /> },
      { path: "english-vocab", element: <EnglishVocabPanel /> },
      { path: "monitor", element: <MonitorMatrix /> },
      { path: "k11-smoke", element: <K11UnifiedSmokeTest /> },
      { path: "gameqa", element: <GameQAPanel /> },
      { path: "os-evidence", element: <OsEvidencePanel /> },
      { path: "pmo", element: <ProjectManagement /> },
      { path: "bi", element: <BIAnalysis /> },
      { path: "network", element: <JachinLink /> },
      { path: "wake", element: <WakeModePanel /> },
      { path: "settings", element: <Persona /> },
      { path: "preferences", element: <SettingsPanel /> },
    ],
  },
];

export const consoleRouter = createHashRouter(consoleRoutes);
