"use client";

import { useCallback } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  ConnectionMode,
  Handle,
  Position,
  type NodeProps,
  type Node,
  type Edge,
} from "reactflow";
import "reactflow/dist/style.css";
import Navbar from "@/components/Navbar";

// --- Custom Node Components ---

type NodeData = {
  label: string;
  accentColor: string;
};

function TriggerNode({ data }: NodeProps<NodeData>) {
  return (
    <div className="rounded-lg bg-zinc-900/95 border border-white/10 shadow-xl min-w-[160px] overflow-hidden">
      <div
        className="h-1 w-full"
        style={{ backgroundColor: data.accentColor || "#22c55e" }}
      />
      <div className="px-4 py-3">
        <p className="text-xs uppercase tracking-wider text-white/50 mb-1">
          Trigger
        </p>
        <p className="text-sm font-medium text-white">{data.label}</p>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-emerald-500 !w-2 !h-2 !border-0" />
    </div>
  );
}

function LLMNode({ data }: NodeProps<NodeData>) {
  return (
    <div className="rounded-lg bg-zinc-900/95 border border-white/10 shadow-xl min-w-[160px] overflow-hidden">
      <div
        className="h-1 w-full"
        style={{ backgroundColor: data.accentColor || "#a78bfa" }}
      />
      <div className="px-4 py-3">
        <p className="text-xs uppercase tracking-wider text-white/50 mb-1">
          LLM
        </p>
        <p className="text-sm font-medium text-white">{data.label}</p>
      </div>
      <Handle type="target" position={Position.Top} className="!bg-violet-500 !w-2 !h-2 !border-0" />
      <Handle type="source" position={Position.Bottom} className="!bg-violet-500 !w-2 !h-2 !border-0" />
    </div>
  );
}

function ActionNode({ data }: NodeProps<NodeData>) {
  return (
    <div className="rounded-lg bg-zinc-900/95 border border-white/10 shadow-xl min-w-[160px] overflow-hidden">
      <div
        className="h-1 w-full"
        style={{ backgroundColor: data.accentColor || "#3b82f6" }}
      />
      <div className="px-4 py-3">
        <p className="text-xs uppercase tracking-wider text-white/50 mb-1">
          Action
        </p>
        <p className="text-sm font-medium text-white">{data.label}</p>
      </div>
      <Handle type="target" position={Position.Top} className="!bg-blue-500 !w-2 !h-2 !border-0" />
    </div>
  );
}

const nodeTypes = {
  trigger: TriggerNode,
  llm: LLMNode,
  action: ActionNode,
};

// --- Initial Flow ---

const initialNodes: Node<NodeData>[] = [
  {
    id: "voice-input",
    type: "trigger",
    position: { x: 250, y: 80 },
    data: { label: "语音输入", accentColor: "#22c55e" },
  },
  {
    id: "intent-llm",
    type: "llm",
    position: { x: 240, y: 220 },
    data: { label: "意图分析 LLM", accentColor: "#a78bfa" },
  },
  {
    id: "exec-code",
    type: "action",
    position: { x: 250, y: 360 },
    data: { label: "执行代码", accentColor: "#3b82f6" },
  },
];

const initialEdges: Edge[] = [
  {
    id: "e-voice-intent",
    source: "voice-input",
    target: "intent-llm",
    animated: true,
  },
  {
    id: "e-intent-exec",
    source: "intent-llm",
    target: "exec-code",
    animated: true,
  },
];

// --- Tool Panel Items ---

const TOOL_ITEMS = [
  { type: "trigger", label: "语音输入", color: "#22c55e", icon: "🎤" },
  { type: "trigger", label: "定时任务", color: "#22c55e", icon: "⏰" },
  { type: "llm", label: "Qwen 路由分析", color: "#a78bfa", icon: "🧠" },
  { type: "llm", label: "意图解析", color: "#a78bfa", icon: "💬" },
  { type: "action", label: "搜索天气", color: "#3b82f6", icon: "🌤" },
  { type: "action", label: "执行代码", color: "#3b82f6", icon: "⚡" },
];

export default function ForgePage() {
  const [nodes, , onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges]
  );

  return (
    <div className="h-screen flex flex-col">
      {/* Background */}
      <div
        className="fixed inset-0 -z-10"
        style={{
          background: `
            radial-gradient(ellipse 80% 50% at 50% 0%, rgba(88, 28, 135, 0.15) 0%, transparent 50%),
            #050505
          `,
        }}
      />

      <Navbar />

      <div className="flex-1 flex pt-16">
        {/* Left Tool Panel */}
        <div className="w-56 shrink-0 p-4">
          <div className="backdrop-blur-xl bg-black/30 border border-white/10 rounded-xl p-4">
            <h3 className="text-xs font-semibold uppercase tracking-widest text-white/50 mb-4">
              节点组件库
            </h3>
            <div className="space-y-2">
              {TOOL_ITEMS.map((item) => (
                <div
                  key={`${item.type}-${item.label}`}
                  className="
                    flex items-center gap-3 px-3 py-2.5 rounded-lg
                    bg-white/5 border border-white/5
                    cursor-grab active:cursor-grabbing
                    hover:bg-white/10 hover:border-white/10
                    transition-colors
                  "
                >
                  <span
                    className="w-1.5 h-8 rounded-full shrink-0"
                    style={{ backgroundColor: item.color }}
                  />
                  <span className="text-lg">{item.icon}</span>
                  <span className="text-sm text-white/80 truncate">{item.label}</span>
                </div>
              ))}
            </div>
            <p className="text-[10px] text-white/30 mt-4">
              拖拽到画布（UI 展示）
            </p>
          </div>
        </div>

        {/* React Flow Canvas */}
        <div className="flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodeTypes={nodeTypes}
            connectionMode={ConnectionMode.Loose}
            fitView
            className="bg-transparent"
            style={{ background: "transparent" }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={20}
              size={1}
              color="rgba(255,255,255,0.08)"
              className="!bg-transparent"
            />
            <Controls
              className="!bg-zinc-900/80 !border-white/10 !rounded-lg [&>button]:!bg-transparent [&>button]:!text-white/70 [&>button:hover]:!bg-white/10 [&>button:hover]:!text-white"
            />
            <MiniMap
              className="!bg-zinc-900/80 !border-white/10"
              nodeColor={(node) => {
                const d = node.data as NodeData;
                return d.accentColor || "#666";
              }}
              maskColor="rgba(5,5,5,0.8)"
            />
          </ReactFlow>
        </div>
      </div>
    </div>
  );
}
