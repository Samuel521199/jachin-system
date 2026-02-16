/**
 * Calendar - 独立日历/待办/提醒
 *
 * 支持事件、提醒、待办，含循环规则（每分钟/小时/天/周/月）
 * 未来可对接外部日历或作为时间触发器
 */

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { Calendar as CalIcon, Plus, Trash2, Check, Clock, Loader2 } from "lucide-react";
import {
  getCalendarItems,
  createCalendarItem,
  updateCalendarItem,
  deleteCalendarItem,
  type CalendarItem,
} from "../../lib/api";
import { cn } from "../../utils/cn";

const RECURRENCE_OPTIONS = [
  { id: "none", label: "不循环" },
  { id: "minute", label: "每分钟" },
  { id: "hourly", label: "每小时" },
  { id: "daily", label: "每天" },
  { id: "weekly", label: "每周" },
  { id: "monthly", label: "每月" },
];

const TYPE_OPTIONS = [
  { id: "event", label: "事件" },
  { id: "reminder", label: "提醒" },
  { id: "todo", label: "待办" },
];

function formatDateTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function Calendar() {
  const [items, setItems] = useState<CalendarItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "event" | "reminder" | "todo">("all");
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState<{
    title: string;
    item_type: "event" | "reminder" | "todo";
    start_at: string;
    recurrence: string;
  }>({
    title: "",
    item_type: "reminder",
    start_at: "",
    recurrence: "none",
  });

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      const type = filter === "all" ? undefined : filter;
      const res = await getCalendarItems({
        item_type: type,
        include_done: filter === "todo",
        days: 30,
      });
      setItems(res.items ?? []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    fetchItems();
    const t = setInterval(fetchItems, 30000);
    return () => clearInterval(t);
  }, [fetchItems]);

  const handleAdd = async () => {
    if (!addForm.title.trim()) return;
    const now = new Date();
    const startAt = addForm.start_at
      ? new Date(addForm.start_at).toISOString()
      : new Date(now.getTime() + 60 * 60 * 1000).toISOString(); // 默认 1 小时后
    try {
      await createCalendarItem({
        title: addForm.title.trim(),
        item_type: addForm.item_type,
        start_at: startAt,
        recurrence: addForm.recurrence,
      });
      setAddForm({ title: "", item_type: "reminder", start_at: "", recurrence: "none" });
      setShowAdd(false);
      fetchItems();
    } catch (e) {
      console.error(e);
    }
  };

  const handleToggleDone = async (item: CalendarItem) => {
    try {
      await updateCalendarItem(item.id, { is_done: !item.is_done });
      fetchItems();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm("确定删除？")) return;
    try {
      await deleteCalendarItem(id);
      fetchItems();
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="h-full flex flex-col p-6 overflow-auto">
      <header className="flex-shrink-0 mb-6 flex items-center justify-between">
        <div>
          <h1
            className="font-sci-fi text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-rose-400 to-rose-600"
            style={{ fontFamily: "Orbitron, sans-serif" }}
          >
            日历与提醒
          </h1>
          <p className="text-slate-500 text-sm mt-0.5">
            独立事件/提醒/待办，支持循环。可与 AI 对话添加，如「明天下午 3 点提醒我开会」
          </p>
        </div>
        <button
          onClick={() => setShowAdd(!showAdd)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-rose-500/20 border border-rose-500/40 text-rose-300 hover:bg-rose-500/30 font-mono text-sm"
        >
          <Plus className="w-4 h-4" />
          添加
        </button>
      </header>

      {showAdd && (
        <motion.div
          className="glass-panel rounded-xl p-4 mb-6"
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <input
              placeholder="标题"
              value={addForm.title}
              onChange={(e) => setAddForm((p) => ({ ...p, title: e.target.value }))}
              className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white font-mono"
            />
            <select
              value={addForm.item_type}
              onChange={(e) =>
                setAddForm((p) => ({ ...p, item_type: e.target.value as "event" | "reminder" | "todo" }))
              }
              className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white font-mono"
            >
              {TYPE_OPTIONS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
            <input
              type="datetime-local"
              value={addForm.start_at}
              onChange={(e) => setAddForm((p) => ({ ...p, start_at: e.target.value }))}
              className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white font-mono"
            />
            <select
              value={addForm.recurrence}
              onChange={(e) => setAddForm((p) => ({ ...p, recurrence: e.target.value }))}
              className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white font-mono"
            >
              {RECURRENCE_OPTIONS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex gap-2 mt-3">
            <button
              onClick={handleAdd}
              className="px-4 py-2 rounded-lg bg-rose-600 text-white font-mono text-sm hover:bg-rose-500"
            >
              创建
            </button>
            <button
              onClick={() => setShowAdd(false)}
              className="px-4 py-2 rounded-lg border border-white/20 text-slate-400 font-mono text-sm"
            >
              取消
            </button>
          </div>
        </motion.div>
      )}

      <div className="flex gap-2 mb-4">
        {(["all", "event", "reminder", "todo"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={cn(
              "px-3 py-1.5 rounded-lg font-mono text-sm",
              filter === f
                ? "bg-rose-500/20 text-rose-300 border border-rose-500/40"
                : "bg-white/5 text-slate-400 border border-white/10 hover:bg-white/10"
            )}
          >
            {f === "all" ? "全部" : f === "event" ? "事件" : f === "reminder" ? "提醒" : "待办"}
          </button>
        ))}
      </div>

      <div className="flex-1 min-h-0 overflow-auto">
        {loading ? (
          <div className="flex items-center gap-2 text-slate-400 font-mono">
            <Loader2 className="w-4 h-4 animate-spin" />
            加载中...
          </div>
        ) : items.length === 0 ? (
          <div className="text-slate-500 font-mono text-sm py-8">
            暂无条目。点击「添加」或对 AI 说「明天下午 3 点提醒我开会」。
          </div>
        ) : (
          <ul className="space-y-2">
            {items.map((item) => (
              <motion.li
                key={item.id}
                className={cn(
                  "glass-panel rounded-xl p-4 flex items-center gap-4",
                  item.is_done && "opacity-60"
                )}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
              >
                <button
                  onClick={() => handleToggleDone(item)}
                  className={cn(
                    "w-8 h-8 rounded-lg border flex items-center justify-center flex-shrink-0",
                    item.is_done
                      ? "bg-emerald-500/20 border-emerald-500/40"
                      : "border-white/20 hover:border-rose-500/40"
                  )}
                >
                  {item.is_done && <Check className="w-4 h-4 text-emerald-400" />}
                </button>
                <div className="flex-1 min-w-0">
                  <div className="font-mono text-white truncate">{item.title}</div>
                  <div className="flex items-center gap-2 mt-0.5 text-slate-500 text-xs font-mono">
                    <Clock className="w-3 h-3" />
                    {formatDateTime(item.start_at)}
                    {item.recurrence !== "none" && (
                      <span className="text-rose-400/80">· {RECURRENCE_OPTIONS.find((r) => r.id === item.recurrence)?.label ?? item.recurrence}</span>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(item.id)}
                  className="p-2 rounded-lg text-slate-400 hover:text-red-400 hover:bg-red-500/10"
                  title="删除"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </motion.li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
