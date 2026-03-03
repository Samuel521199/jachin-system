"use client";

import { Handle, Position, type NodeProps } from "reactflow";
import { Mic, Cpu, Zap } from "lucide-react";

export interface ForgeNodeData {
  label: string;
  pluginId?: string;
  price?: string;
}

export function TriggerNode({ data }: NodeProps<ForgeNodeData>) {
  return (
    <div className="rounded-xl min-w-[180px] overflow-hidden bg-blue-900/30 border border-blue-500/30 shadow-[0_0_15px_rgba(59,130,246,0.2)] backdrop-blur-sm">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-white/10">
        <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
        <span className="text-[10px] uppercase tracking-widest text-cyan-400/80 font-mono">Trigger</span>
      </div>
      <div className="px-4 py-3 flex items-center gap-3">
        <Mic className="w-5 h-5 text-cyan-400/80 shrink-0" />
        <p className="text-sm font-mono text-white/95 truncate">{data.label}</p>
      </div>
      <Handle type="source" position={Position.Right} className="!w-3 !h-3 !bg-cyan-400 !border-2 !border-cyan-200 !shadow-[0_0_10px_rgba(34,211,238,0.8)]" />
    </div>
  );
}

export function ProcessorNode({ data }: NodeProps<ForgeNodeData>) {
  return (
    <div className="rounded-xl min-w-[180px] overflow-hidden bg-purple-900/30 border border-purple-500/50 shadow-[0_0_15px_rgba(168,85,247,0.4)] backdrop-blur-sm">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-white/10">
        <span className="w-2 h-2 rounded-full bg-purple-400 animate-pulse" />
        <span className="text-[10px] uppercase tracking-widest text-purple-400/80 font-mono">Processor</span>
      </div>
      <div className="px-4 py-3 flex items-center gap-3">
        <Cpu className="w-5 h-5 text-purple-400/80 shrink-0" />
        <p className="text-sm font-mono text-white/95 truncate">{data.label}</p>
      </div>
      <Handle type="target" position={Position.Left} className="!w-3 !h-3 !bg-purple-400 !border-2 !border-purple-200 !shadow-[0_0_10px_rgba(168,85,247,0.8)]" />
      <Handle type="source" position={Position.Right} className="!w-3 !h-3 !bg-purple-400 !border-2 !border-purple-200 !shadow-[0_0_10px_rgba(168,85,247,0.8)]" />
    </div>
  );
}

export function ActionNode({ data }: NodeProps<ForgeNodeData>) {
  return (
    <div className="rounded-xl min-w-[180px] overflow-hidden bg-green-900/30 border border-green-500/30 shadow-[0_0_15px_rgba(34,197,94,0.2)] backdrop-blur-sm">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-white/10">
        <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
        <span className="text-[10px] uppercase tracking-widest text-green-400/80 font-mono">Action</span>
      </div>
      <div className="px-4 py-3 flex items-center gap-3">
        <Zap className="w-5 h-5 text-green-400/80 shrink-0" />
        <p className="text-sm font-mono text-white/95 truncate">{data.label}</p>
      </div>
      <Handle type="target" position={Position.Left} className="!w-3 !h-3 !bg-green-400 !border-2 !border-green-200 !shadow-[0_0_10px_rgba(34,197,94,0.8)]" />
    </div>
  );
}

export const forgeNodeTypes = {
  trigger: TriggerNode,
  processor: ProcessorNode,
  action: ActionNode,
};
