"use client";

import type { ReactNode } from "react";
import ErrorBoundaryWrapper from "@/components/ErrorBoundaryWrapper";
import { SessionProvider } from "@/components/SessionProvider";
import { NexusUiLangProvider } from "@/components/NexusUiLangProvider";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <NexusUiLangProvider>
        <ErrorBoundaryWrapper>{children}</ErrorBoundaryWrapper>
      </NexusUiLangProvider>
    </SessionProvider>
  );
}
