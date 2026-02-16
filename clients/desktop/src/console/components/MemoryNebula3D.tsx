/**
 * MemoryNebula3D - 记忆星云 3D 可视化
 * 使用 @react-three/fiber 渲染：记忆点以 3D 粒子形式漂浮，支持搜索高亮
 */

import { useMemo, Suspense } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import type { MemorySearchResult } from "../../lib/api";

const SEED = 42;
function seeded(i: number) {
  const x = Math.sin(SEED + i * 1.5) * 10000;
  return x - Math.floor(x);
}

/** 背景星云粒子 */
function BackgroundNebula() {
  const count = 320;
  const { pos, col } = useMemo(() => {
    const p = new Float32Array(count * 3);
    const c = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      p[i * 3] = (seeded(i) - 0.5) * 12;
      p[i * 3 + 1] = (seeded(i + 10) - 0.5) * 12;
      p[i * 3 + 2] = (seeded(i + 20) - 0.5) * 12;
      const t = seeded(i + 30);
      c[i * 3] = 0.4 + t * 0.3;
      c[i * 3 + 1] = 0.6 + t * 0.2;
      c[i * 3 + 2] = 0.9 + t * 0.1;
    }
    return { pos: p, col: c };
  }, []);

  return (
    <points>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" array={pos} count={count} itemSize={3} />
        <bufferAttribute attach="attributes-color" array={col} count={count} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial
        transparent
        vertexColors
        size={0.08}
        sizeAttenuation
        depthWrite={false}
        opacity={0.6}
      />
    </points>
  );
}

/** 记忆搜索结果点云 */
function MemoryPoints({ results }: { results: MemorySearchResult[] }) {
  const count = results.length;
  const { pos, col } = useMemo(() => {
    const p = new Float32Array(count * 3);
    const c = new Float32Array(count * 3);
    results.forEach((r, i) => {
      const h = (r.id || "").split("").reduce((a, c) => a + c.charCodeAt(0), 0);
      p[i * 3] = Math.sin(h * 0.1) * 4;
      p[i * 3 + 1] = Math.cos(h * 0.07) * 4;
      p[i * 3 + 2] = Math.sin(h * 0.13) * 3;
      const score = Math.min(1, r.score);
      c[i * 3] = 0.2 + score * 0.5;
      c[i * 3 + 1] = 0.7 + score * 0.3;
      c[i * 3 + 2] = 1;
    });
    return { pos: p, col: c };
  }, [results]);

  if (count === 0) return null;

  return (
    <points>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" array={pos} count={count} itemSize={3} />
        <bufferAttribute attach="attributes-color" array={col} count={count} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial
        transparent
        vertexColors
        size={0.18}
        sizeAttenuation
        depthWrite={false}
        opacity={0.9}
      />
    </points>
  );
}

function Scene({ results }: { results: MemorySearchResult[] }) {
  return (
    <>
      <ambientLight intensity={0.4} />
      <pointLight position={[5, 5, 5]} intensity={1} color="#94a3b8" />
      <pointLight position={[-5, -3, 2]} intensity={0.5} color="#f472b6" />
      <BackgroundNebula />
      <MemoryPoints results={results} />
      <OrbitControls
        enableZoom
        enablePan={false}
        minDistance={4}
        maxDistance={20}
        autoRotate
        autoRotateSpeed={0.4}
      />
    </>
  );
}

export function MemoryNebula3D({
  results = [],
  className,
}: {
  results?: MemorySearchResult[];
  className?: string;
}) {
  return (
    <div className={className} style={{ minHeight: 240, background: "linear-gradient(180deg, rgba(15,23,42,0.6) 0%, rgba(15,23,42,0.9) 100%)" }}>
      <Canvas
        camera={{ position: [0, 0, 8], fov: 50 }}
        gl={{ antialias: true, alpha: true }}
        dpr={[1, 2]}
      >
        <Suspense fallback={null}>
          <Scene results={results} />
        </Suspense>
      </Canvas>
    </div>
  );
}
