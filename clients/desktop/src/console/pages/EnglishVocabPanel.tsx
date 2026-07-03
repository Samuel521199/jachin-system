import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { BookOpen, CheckCircle2, ExternalLink, HelpCircle, Loader2, RefreshCw, RotateCcw, XCircle } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { englishWordBooks } from "../../components/EnglishVocab/wordBooks";

type WordProgress = {
  seen: number;
  known: number;
  fuzzy: number;
  unknown: number;
  status: "new" | "learning" | "known";
  last_seen_at?: number;
  due_at?: number;
};

type DailyStats = {
  total: number;
  known: number;
  fuzzy: number;
  unknown: number;
};

type VocabState = {
  selected_book_id: string;
  progress: Record<string, WordProgress>;
  daily: Record<string, DailyStats>;
  state_path: string;
};

function dayKey(offset: number) {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const y = d.getFullYear();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function progressPrefix(bookId: string) {
  return `${bookId}:`;
}

function sumDaily(daily: Record<string, DailyStats>) {
  return Object.values(daily).reduce(
    (acc, row) => ({
      total: acc.total + Number(row.total || 0),
      known: acc.known + Number(row.known || 0),
      fuzzy: acc.fuzzy + Number(row.fuzzy || 0),
      unknown: acc.unknown + Number(row.unknown || 0),
    }),
    { total: 0, known: 0, fuzzy: 0, unknown: 0 },
  );
}

function StatCard({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  value: number | string;
  icon: typeof BookOpen;
  tone: "cyan" | "emerald" | "amber" | "rose";
}) {
  const toneClass =
    tone === "emerald"
      ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200"
      : tone === "amber"
        ? "border-amber-400/25 bg-amber-500/10 text-amber-200"
        : tone === "rose"
          ? "border-rose-400/25 bg-rose-500/10 text-rose-200"
          : "border-cyan-400/25 bg-cyan-500/10 text-cyan-200";
  return (
    <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-slate-400">{label}</span>
        <span className={`flex h-8 w-8 items-center justify-center rounded-md border ${toneClass}`}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <div className="mt-3 text-3xl font-semibold text-white">{value}</div>
    </div>
  );
}

export function EnglishVocabPanel() {
  const [state, setState] = useState<VocabState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [openingCard, setOpeningCard] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await invoke<VocabState>("english_vocab_state_get");
      setState(next);
      setNotice(null);
    } catch (e) {
      setNotice(`加载失败：${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const activeBook = useMemo(
    () => englishWordBooks.find((book) => book.id === state?.selected_book_id) ?? englishWordBooks[0],
    [state?.selected_book_id],
  );

  const totals = useMemo(() => sumDaily(state?.daily ?? {}), [state?.daily]);

  const bookProgress = useMemo(() => {
    const prefix = progressPrefix(activeBook.id);
    return Object.entries(state?.progress ?? {}).filter(([key]) => key.startsWith(prefix));
  }, [activeBook.id, state?.progress]);

  const learnedInBook = bookProgress.length;
  const knownInBook = bookProgress.filter(([, item]) => item.status === "known").length;
  const learningInBook = bookProgress.filter(([, item]) => item.status === "learning").length;
  const bookPercent = activeBook.words.length ? Math.round((learnedInBook / activeBook.words.length) * 100) : 0;

  const chartRows = useMemo(
    () =>
      Array.from({ length: 14 }, (_, index) => {
        const key = dayKey(index - 13);
        const row = state?.daily?.[key] ?? { total: 0, known: 0, fuzzy: 0, unknown: 0 };
        return {
          day: key.slice(5),
          total: row.total,
          known: row.known,
          fuzzy: row.fuzzy,
          unknown: row.unknown,
        };
      }),
    [state?.daily],
  );

  const setBook = async (bookId: string) => {
    setBusy(true);
    try {
      const next = await invoke<VocabState>("english_vocab_state_set_book", { input: { book_id: bookId } });
      setState(next);
      setNotice("词书已切换，前台小窗会自动同步。");
    } catch (e) {
      setNotice(`切换失败：${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const openCard = async () => {
    setOpeningCard(true);
    try {
      await invoke("show_english_vocab_window");
      setNotice("背词卡片已打开。如果没有出现在前台，请查看屏幕右下角。");
    } catch (e) {
      setNotice(`打开背词卡片失败：${String(e)}`);
    } finally {
      setOpeningCard(false);
    }
  };

  const reset = async () => {
    if (!window.confirm("确认清空英语学习记录？")) return;
    setBusy(true);
    try {
      const next = await invoke<VocabState>("english_vocab_state_reset");
      setState(next);
      setNotice("学习记录已清空。");
    } catch (e) {
      setNotice(`重置失败：${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="console-fiber-host console-holo-slab flex h-full min-h-0 flex-col overflow-hidden p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-cyan-400/15 pb-4">
        <div>
          <p className="text-xs uppercase tracking-[0.32em] text-cyan-300/70">English Learning Console</p>
          <h1 className="mt-1 text-2xl font-semibold text-cyan-50">英语学习后台</h1>
          <p className="mt-1 text-sm text-slate-400">词书配置、学习统计和趋势分析集中在这里，前台小窗只保留背词动作。</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="inline-flex h-10 items-center gap-2 rounded-md border border-emerald-400/25 bg-emerald-500/10 px-3 text-sm text-emerald-100 transition hover:bg-emerald-400/15 disabled:opacity-50"
            onClick={() => void openCard()}
            disabled={loading || busy || openingCard}
            title="手动打开右下角英语背词卡片"
          >
            {openingCard ? <Loader2 className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}
            打开卡片
          </button>
          <button
            className="inline-flex h-10 items-center gap-2 rounded-md border border-cyan-400/25 bg-cyan-500/10 px-3 text-sm text-cyan-100 transition hover:bg-cyan-400/15 disabled:opacity-50"
            onClick={() => void load()}
            disabled={loading || busy || openingCard}
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            刷新
          </button>
          <button
            className="inline-flex h-10 items-center gap-2 rounded-md border border-rose-400/25 bg-rose-500/10 px-3 text-sm text-rose-100 transition hover:bg-rose-400/15 disabled:opacity-50"
            onClick={() => void reset()}
            disabled={loading || busy || openingCard}
          >
            <RotateCcw className="h-4 w-4" />
            重置记录
          </button>
        </div>
      </div>

      {notice ? <div className="mt-3 rounded-md border border-cyan-400/20 bg-cyan-500/10 px-3 py-2 text-sm text-cyan-100">{notice}</div> : null}

      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-4">
        <StatCard label="累计学习" value={totals.total} icon={BookOpen} tone="cyan" />
        <StatCard label="认识" value={totals.known} icon={CheckCircle2} tone="emerald" />
        <StatCard label="模糊" value={totals.fuzzy} icon={HelpCircle} tone="amber" />
        <StatCard label="不认识" value={totals.unknown} icon={XCircle} tone="rose" />
      </div>

      <div className="mt-4 grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-[22rem_minmax(0,1fr)]">
        <section className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
          <div className="flex items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold text-cyan-50">词书选择</h2>
              <p className="mt-1 text-xs text-slate-500">选择后前台小窗会按这个词书出词。</p>
            </div>
            {busy ? <Loader2 className="h-4 w-4 animate-spin text-cyan-200" /> : null}
          </div>

          <div className="mt-4 space-y-2">
            {englishWordBooks.map((book) => {
              const active = book.id === activeBook.id;
              return (
                <button
                  key={book.id}
                  className={`w-full rounded-md border px-3 py-3 text-left transition ${
                    active
                      ? "border-cyan-300/45 bg-cyan-400/10 text-cyan-50"
                      : "border-white/10 bg-white/[0.03] text-slate-300 hover:border-cyan-300/25 hover:bg-cyan-400/5"
                  }`}
                  onClick={() => void setBook(book.id)}
                  disabled={busy}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{book.title}</span>
                    <span className="text-[11px] text-slate-500">{book.words.length} 词</span>
                  </div>
                  <div className="mt-1 text-xs text-slate-500">{book.subtitle}</div>
                </button>
              );
            })}
          </div>

          <div className="mt-4 rounded-md border border-white/10 bg-white/[0.03] p-3">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>当前词书进度</span>
              <span>{bookPercent}%</span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800">
              <div className="h-full rounded-full bg-cyan-300" style={{ width: `${bookPercent}%` }} />
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
              <div className="rounded border border-white/10 bg-white/[0.03] p-2">
                <div className="text-slate-500">已学</div>
                <div className="mt-1 text-cyan-100">{learnedInBook}</div>
              </div>
              <div className="rounded border border-white/10 bg-white/[0.03] p-2">
                <div className="text-slate-500">认识</div>
                <div className="mt-1 text-emerald-100">{knownInBook}</div>
              </div>
              <div className="rounded border border-white/10 bg-white/[0.03] p-2">
                <div className="text-slate-500">复习</div>
                <div className="mt-1 text-amber-100">{learningInBook}</div>
              </div>
            </div>
          </div>
        </section>

        <section className="flex min-h-0 flex-col rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold text-cyan-50">最近 14 天趋势</h2>
              <p className="mt-1 text-xs text-slate-500">按每日判断次数统计学习节奏。</p>
            </div>
            <span className="rounded-md border border-cyan-300/20 bg-cyan-400/10 px-2 py-1 text-xs text-cyan-100">
              状态文件：{state?.state_path || "-"}
            </span>
          </div>

          <div className="mt-4 min-h-[280px] flex-1">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartRows} margin={{ top: 12, right: 18, bottom: 8, left: 0 }}>
                <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                <XAxis dataKey="day" tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} width={32} allowDecimals={false} />
                <Tooltip
                  contentStyle={{
                    background: "rgba(15,23,42,0.96)",
                    border: "1px solid rgba(34,211,238,0.22)",
                    borderRadius: 8,
                    color: "#e2e8f0",
                  }}
                />
                <Line type="monotone" dataKey="total" name="总量" stroke="#22d3ee" strokeWidth={2.2} dot={false} />
                <Line type="monotone" dataKey="known" name="认识" stroke="#34d399" strokeWidth={1.8} dot={false} />
                <Line type="monotone" dataKey="fuzzy" name="模糊" stroke="#fbbf24" strokeWidth={1.8} dot={false} />
                <Line type="monotone" dataKey="unknown" name="不认识" stroke="#fb7185" strokeWidth={1.8} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>
    </div>
  );
}
