"use client";

import { useCallback, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  ConnectionMode,
  type Node,
  type Edge,
} from "reactflow";
import "reactflow/dist/style.css";
import Navbar from "@/components/Navbar";
import { useNexusUiLang } from "@/components/NexusUiLangProvider";
import Toast from "@/components/Toast";
import { forgeNodeTypes } from "@/components/forge/CustomNodes";
import { NodePalette } from "@/components/forge/NodePalette";
import type { ForgeNodeData } from "@/components/forge/CustomNodes";
import { nexusForge, type NexusUiLang } from "@/lib/nexus-ui-i18n";

function buildInitialForgeNodes(lang: NexusUiLang): Node<ForgeNodeData>[] {
  const labels = nexusForge[lang].nodes;
  return [
    {
      id: "n1",
      type: "trigger",
      position: { x: 100, y: 150 },
      data: { label: labels[0].label },
    },
    {
      id: "n2",
      type: "processor",
      position: { x: 350, y: 150 },
      data: { label: labels[1].label },
    },
    {
      id: "n3",
      type: "action",
      position: { x: 600, y: 150 },
      data: { label: labels[2].label },
    },
  ];
}

const initialEdges: Edge[] = [
  { id: "e1-2", source: "n1", target: "n2", animated: true, style: { stroke: "#22d3ee", strokeWidth: 2 } },
  { id: "e2-3", source: "n2", target: "n3", animated: true, style: { stroke: "#a78bfa", strokeWidth: 2 } },
];

let nodeId = 4;
function getId() {
  return `n${nodeId++}`;
}

function ForgeCanvas() {
  const { lang } = useNexusUiLang();
  const t = nexusForge[lang];
  const { screenToFlowPosition } = useReactFlow();
  const [nodes, setNodes, onNodesChange] = useNodesState(buildInitialForgeNodes(lang));
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [toastVisible, setToastVisible] = useState(false);
  const [toastMessage, setToastMessage] = useState<string>(() => nexusForge[lang].toastDefault);
  const [minting, setMinting] = useState(false);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData("application/reactflow");
      if (!raw) return;
      try {
        const item = JSON.parse(raw) as { type: string; label: string; pluginId?: string; price?: string };
        const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
        const newNode: Node<ForgeNodeData> = {
          id: getId(),
          type: item.type as "trigger" | "processor" | "action",
          position,
          data: { label: item.label, pluginId: item.pluginId, price: item.price },
        };
        setNodes((nds) => nds.concat(newNode));
      } catch {
        // ignore invalid drop
      }
    },
    [screenToFlowPosition, setNodes]
  );

  const onConnect = useCallback(
    (params: Connection) =>
      setEdges((eds) =>
        addEdge(
          { ...params, animated: true, style: { stroke: "#22d3ee", strokeWidth: 2 } },
          eds
        )
      ),
    [setEdges]
  );

  const onMint = useCallback(async () => {
    setMinting(true);
    const ast = {
      version: "1.0",
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.type,
        position: n.position,
        data: n.data,
      })),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
      })),
    };
    console.log("Forge AST:", JSON.stringify(ast, null, 2));

    const payload = {
      name: t.defaultName,
      ast_json: ast,
    };

    try {
      const res = await fetch("/api/v1/blueprints/mint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (!res.ok) {
        setToastMessage(data.error || t.mintFail);
        setToastVisible(true);
        return;
      }

      setToastMessage(data.message || t.mintOk);
      setToastVisible(true);
    } catch (err) {
      setToastMessage((err as Error).message || t.mintNetwork);
      setToastVisible(true);
    } finally {
      setMinting(false);
    }
  }, [nodes, edges, t.defaultName, t.mintFail, t.mintOk, t.mintNetwork]);

  return (
    <>
      <div className="flex-1 relative">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onDrop={onDrop}
          onDragOver={onDragOver}
          nodeTypes={forgeNodeTypes}
          connectionMode={ConnectionMode.Loose}
          fitView
          className="bg-transparent"
          style={{ background: "transparent" }}
          defaultEdgeOptions={{
            animated: true,
            style: { stroke: "#22d3ee", strokeWidth: 2 },
          }}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={24}
            size={1}
            color="rgba(34, 211, 238, 0.12)"
            className="!bg-transparent"
          />
          <Controls />
          <MiniMap
            nodeColor={(node) => {
              const t = node.type as string;
              if (t === "trigger") return "#22d3ee";
              if (t === "processor") return "#a78bfa";
              if (t === "action") return "#22c55e";
              return "#666";
            }}
            maskColor="rgba(5,5,5,0.85)"
          />
        </ReactFlow>
        <button
          onClick={onMint}
          disabled={minting}
          className="absolute top-4 right-4 z-10 px-5 py-2.5 rounded-xl font-semibold bg-cyan-500/20 border border-cyan-500/50 text-cyan-400 hover:bg-cyan-500/30 hover:border-cyan-400/60 transition-all shadow-[0_0_20px_rgba(34,211,238,0.2)] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {minting ? t.minting : t.mintCta}
        </button>
      </div>
      <Toast
        message={toastMessage}
        visible={toastVisible}
        onClose={() => setToastVisible(false)}
      />
    </>
  );
}

export default function ForgePage() {
  const { lang } = useNexusUiLang();
  return (
    <div className="h-screen flex flex-col bg-[#030303]">
      <div
        className="fixed inset-0 -z-10 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 80% 50% at 50% 0%, rgba(34, 211, 238, 0.04) 0%, transparent 50%),
            radial-gradient(ellipse 40% 60% at 80% 80%, rgba(168, 85, 247, 0.03) 0%, transparent 50%),
            #030303
          `,
        }}
      />

      <Navbar />

      <div className="flex-1 flex pt-16 overflow-hidden">
        <NodePalette />
        <ReactFlowProvider>
          <ForgeCanvas key={lang} />
        </ReactFlowProvider>
      </div>
    </div>
  );
}
