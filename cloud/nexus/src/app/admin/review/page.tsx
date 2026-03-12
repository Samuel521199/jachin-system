"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * 重定向到新版审核页面
 */
export default function AdminReviewRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/dashboard/admin/review");
  }, [router]);
  return (
    <div className="min-h-screen flex items-center justify-center bg-[#030712]">
      <p className="text-white/50">跳转中...</p>
    </div>
  );
}
