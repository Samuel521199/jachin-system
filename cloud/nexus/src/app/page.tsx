import Link from "next/link";
import Navbar from "@/components/Navbar";

export default function Home() {
  return (
    <div className="min-h-screen relative overflow-hidden">
      {/* Background: 深邃紫色光晕 */}
      <div
        className="fixed inset-0 -z-10"
        style={{
          background: `
            radial-gradient(ellipse 80% 50% at 50% 0%, rgba(88, 28, 135, 0.25) 0%, transparent 50%),
            radial-gradient(ellipse 60% 40% at 80% 60%, rgba(59, 7, 100, 0.15) 0%, transparent 50%),
            radial-gradient(ellipse 50% 30% at 20% 80%, rgba(88, 28, 135, 0.1) 0%, transparent 50%),
            #050505
          `,
        }}
      />

      <Navbar />

      {/* Hero Section */}
      <section className="min-h-screen flex flex-col items-center justify-center px-6 pt-16">
        <div className="max-w-4xl mx-auto text-center">
          {/* 主标题 - 紫/蓝渐变发光 */}
          <h1
            className="text-5xl sm:text-6xl md:text-7xl font-bold tracking-tight mb-6"
            style={{
              background: "linear-gradient(135deg, #a78bfa 0%, #6366f1 40%, #22d3ee 100%)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
              backgroundClip: "text",
              textShadow: "0 0 60px rgba(139, 92, 246, 0.3)",
            }}
          >
            The Ether of Intelligence.
          </h1>
          <p className="text-white/60 text-lg sm:text-xl mb-12 max-w-2xl mx-auto">
            Download Skills. Update Soul. Keep your Privacy.
          </p>

          {/* CTA Buttons */}
          <div className="flex flex-col sm:flex-row gap-4 justify-center items-center">
            <Link
              href="/market"
              className="
                px-8 py-3.5 rounded-lg font-medium text-white
                border border-violet-500/60
                bg-violet-500/10 hover:bg-violet-500/20
                transition-all duration-300
                animate-pulse-glow
              "
            >
              Enter Neural Market
            </Link>
            <Link
              href="/forge"
              className="
                px-8 py-3.5 rounded-lg font-medium
                text-white/90 hover:text-white
                border border-white/20 hover:border-white/40
                bg-transparent hover:bg-white/5
                transition-all duration-300
              "
            >
              Launch The Forge
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
