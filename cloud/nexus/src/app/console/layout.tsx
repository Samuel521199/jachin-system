"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useSession } from "next-auth/react";

/**
 * 无工作区成员关系时，除「工作区」页外一律重定向到 /console/workspace，完成 onboarding。
 */
export default function ConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { data: session, status } = useSession();
  const path = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (status !== "authenticated" || !session?.user) return;
    const orgId = (session.user.orgId ?? "").trim();
    if (!orgId && path !== "/console/workspace") {
      router.replace("/console/workspace");
    }
  }, [status, session?.user, session?.user?.orgId, path, router]);

  return <>{children}</>;
}
