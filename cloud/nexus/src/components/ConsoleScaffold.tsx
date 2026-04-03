"use client";

import Navbar from "@/components/Navbar";

/**
 * 控制台系页面共用背景与顶栏，避免在 console / fleet / workspace 等处复制一整段布局。
 */
export default function ConsoleScaffold({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-[#050505]">
      <div
        className="fixed inset-0 -z-10 pointer-events-none opacity-30"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%2322d3ee' fill-opacity='0.08'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E")`,
        }}
      />
      <div
        className="fixed inset-0 -z-10 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 60% 40% at 50% 20%, rgba(34, 211, 238, 0.06) 0%, transparent 50%),
            radial-gradient(ellipse 40% 60% at 80% 80%, rgba(168, 85, 247, 0.04) 0%, transparent 50%),
            #050505
          `,
        }}
      />
      <Navbar />
      {children}
    </div>
  );
}
