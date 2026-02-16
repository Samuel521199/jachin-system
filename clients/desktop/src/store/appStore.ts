import { create } from "zustand";

interface AppState {
  isConnected: boolean;
  backendUrl: string;
  daprPort: number;
  setConnected: (connected: boolean) => void;
  setBackendUrl: (url: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  isConnected: false,
  backendUrl: "http://localhost:18888",
  daprPort: 3500,
  setConnected: (connected) => set({ isConnected: connected }),
  setBackendUrl: (url) => set({ backendUrl: url }),
}));
