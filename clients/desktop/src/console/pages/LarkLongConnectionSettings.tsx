/**
 * 飞书长连接 — 本机是否接管各机器人 / PMO 表变更事件
 * 写入 ~/.jachin/config/im_channels.yaml；保存后需重启 Desktop 使 L3 侧车重建连接。
 */
import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Bot, Check, FolderOpen, Loader2, Plus, Radio, Save, Table2, Users, X } from "lucide-react";
import { cn } from "../../utils/cn";

export interface ImChannelEntry {
  enabled: boolean;
  mode?: string;
  app_id?: string;
  chat_ids?: string[];
  exclusive_sessions?: boolean;
  domain?: string;
}

export interface ImBitableChannelEntry {
  enabled: boolean;
  app_id?: string;
  domain?: string;
}

export interface ImChannelsUiConfig {
  path: string;
  exists: boolean;
  seeded: boolean;
  lark: ImChannelEntry;
  lark_hr: ImChannelEntry;
  lark_pmo_bitable: ImBitableChannelEntry;
}

type ChatIdFeedback = { kind: "ok" | "warn" | "err"; text: string } | null;

function normalizeChatId(raw: string): string {
  return raw.trim();
}

function isValidChatId(id: string): boolean {
  return /^oc_[a-z0-9]+$/i.test(id);
}

function dedupeChatIds(ids: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of ids) {
    const id = normalizeChatId(raw);
    if (!id) continue;
    const key = id.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(id);
  }
  return out;
}

function Toggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <label className="flex items-center gap-3 cursor-pointer select-none">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 rounded-full border transition-colors",
          checked ? "bg-emerald-600/80 border-emerald-500/50" : "bg-white/10 border-white/15",
          disabled && "opacity-50 cursor-not-allowed"
        )}
      >
        <span
          className={cn(
            "pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform mt-0.5",
            checked ? "translate-x-5" : "translate-x-0.5"
          )}
        />
      </button>
      <span className="text-sm text-slate-200">{label}</span>
    </label>
  );
}

function ChatIdListEditor({
  ids,
  onChange,
  disabled,
  emptyHint,
  exclusiveSessions,
}: {
  ids: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
  emptyHint: string;
  exclusiveSessions: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [feedback, setFeedback] = useState<ChatIdFeedback>(null);

  const flash = (next: ChatIdFeedback) => {
    setFeedback(next);
    if (next) {
      window.setTimeout(() => setFeedback(null), 2800);
    }
  };

  const tryAdd = (raw: string) => {
    const id = normalizeChatId(raw);
    if (!id) {
      flash({ kind: "warn", text: "请输入会话 ID" });
      return;
    }
    if (!isValidChatId(id)) {
      flash({ kind: "err", text: "格式应为 oc_ 开头的飞书会话 ID" });
      return;
    }
    if (ids.some((x) => x.toLowerCase() === id.toLowerCase())) {
      flash({ kind: "warn", text: "该会话已在列表中" });
      setDraft("");
      return;
    }
    onChange([...ids, id]);
    setDraft("");
    flash({ kind: "ok", text: `已添加 ${id.slice(0, 12)}…` });
  };

  const removeAt = (index: number) => {
    const removed = ids[index];
    onChange(ids.filter((_, i) => i !== index));
    if (removed) {
      flash({ kind: "ok", text: `已移除 ${removed.slice(0, 12)}…` });
    }
  };

  return (
    <div className="space-y-2">
      <div
        className={cn(
          "rounded-lg border border-white/10 bg-black/25 p-3 min-h-[72px] transition-colors",
          feedback?.kind === "ok" && "border-emerald-500/30",
          feedback?.kind === "err" && "border-rose-500/40"
        )}
      >
        {ids.length === 0 ? (
          <p className="text-xs text-slate-500 leading-relaxed py-1">{emptyHint}</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {ids.map((id, index) => (
              <li
                key={`${id}-${index}`}
                className="inline-flex items-center gap-1.5 rounded-full border border-cyan-500/25 bg-cyan-950/40 pl-3 pr-1.5 py-1 text-xs font-mono text-cyan-100/90"
              >
                <span className="max-w-[240px] truncate" title={id}>
                  {id}
                </span>
                <button
                  type="button"
                  disabled={disabled}
                  aria-label={`移除 ${id}`}
                  onClick={() => removeAt(index)}
                  className="rounded-full p-0.5 text-slate-400 hover:bg-white/10 hover:text-rose-300 disabled:opacity-40"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <input
          type="text"
          value={draft}
          disabled={disabled}
          placeholder="粘贴 oc_ 开头的 chat_id"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              tryAdd(draft);
            }
          }}
          className="min-w-[200px] flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-xs font-mono text-slate-200 placeholder:text-slate-600 focus:border-cyan-500/40 focus:outline-none"
        />
        <button
          type="button"
          disabled={disabled || !draft.trim()}
          onClick={() => tryAdd(draft)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-colors",
            draft.trim()
              ? "border-cyan-500/40 bg-cyan-600/20 text-cyan-100 hover:bg-cyan-600/35"
              : "border-white/10 text-slate-500 cursor-not-allowed"
          )}
        >
          <Plus className="h-3.5 w-3.5" />
          添加会话
        </button>
      </div>

      {feedback && (
        <p
          className={cn(
            "flex items-center gap-1.5 text-xs",
            feedback.kind === "ok" && "text-emerald-400",
            feedback.kind === "warn" && "text-amber-400",
            feedback.kind === "err" && "text-rose-400"
          )}
        >
          {feedback.kind === "ok" && <Check className="h-3.5 w-3.5 shrink-0" />}
          {feedback.text}
        </p>
      )}

      {ids.length > 0 && (
        <p className="text-[11px] text-slate-600">
          {exclusiveSessions
            ? `已绑定 ${ids.length} 个会话；白名单模式下仅处理上述会话。`
            : `已标记 ${ids.length} 个会话；当前为默认节点，仍处理长连接上的全部会话。`}
        </p>
      )}
    </div>
  );
}

export function LarkLongConnectionSettings() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [configPath, setConfigPath] = useState("");
  const [larkEnabled, setLarkEnabled] = useState(false);
  const [larkHrEnabled, setLarkHrEnabled] = useState(false);
  const [bitableEnabled, setBitableEnabled] = useState(false);
  const [larkChatIds, setLarkChatIds] = useState<string[]>([]);
  const [larkHrChatIds, setLarkHrChatIds] = useState<string[]>([]);
  const [larkExclusiveSessions, setLarkExclusiveSessions] = useState(true);
  const [larkHrExclusiveSessions, setLarkHrExclusiveSessions] = useState(true);
  const [domain, setDomain] = useState("https://open.feishu.cn");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const cfg = await invoke<ImChannelsUiConfig>("read_im_channels_config");
      setConfigPath(cfg.path);
      setLarkEnabled(Boolean(cfg.lark?.enabled));
      setLarkHrEnabled(Boolean(cfg.lark_hr?.enabled));
      setBitableEnabled(Boolean(cfg.lark_pmo_bitable?.enabled));
      const larkIds = dedupeChatIds(cfg.lark?.chat_ids ?? []);
      const hrIds = dedupeChatIds(cfg.lark_hr?.chat_ids ?? []);
      setLarkChatIds(larkIds);
      setLarkHrChatIds(hrIds);
      setLarkExclusiveSessions(cfg.lark?.exclusive_sessions ?? larkIds.length > 0);
      setLarkHrExclusiveSessions(cfg.lark_hr?.exclusive_sessions ?? hrIds.length > 0);
      setDomain(cfg.lark?.domain || cfg.lark_hr?.domain || "https://open.feishu.cn");
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await invoke<ImChannelsUiConfig>("write_im_channels_config", {
        patch: {
          lark: {
            enabled: larkEnabled,
            mode: "long_connection",
            app_id: "",
            chat_ids: dedupeChatIds(larkChatIds),
            exclusive_sessions:
              dedupeChatIds(larkChatIds).length > 0 ? larkExclusiveSessions : false,
            domain,
          },
          lark_hr: {
            enabled: larkHrEnabled,
            mode: "long_connection",
            app_id: "",
            chat_ids: dedupeChatIds(larkHrChatIds),
            exclusive_sessions:
              dedupeChatIds(larkHrChatIds).length > 0 ? larkHrExclusiveSessions : false,
            domain,
          },
          lark_pmo_bitable: {
            enabled: bitableEnabled,
            app_id: "",
            domain,
          },
        },
      });
      setSaved(true);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const openConfigDir = async () => {
    try {
      await invoke<string>("open_im_channels_config_dir");
    } catch (e) {
      setError(String(e));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-slate-400 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载飞书长连接配置…
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <Radio className="h-5 w-5 text-cyan-400 mt-0.5 shrink-0" />
        <div>
          <h3 className="text-base font-medium text-slate-100">飞书长连接（本机接管）</h3>
          <p className="text-xs text-slate-500 mt-1 leading-relaxed">
            控制<strong className="text-slate-400">本电脑</strong>是否建立飞书 WebSocket，接收机器人私聊/群消息或
            PMO 表变更。同一飞书应用同时只应有一台电脑开启对应开关。凭证仍从安装目录{" "}
            <code className="text-cyan-300/90">.env</code> /{" "}
            <code className="text-cyan-300/90">LARK_*</code> /{" "}
            <code className="text-cyan-300/90">HR_LARK_*</code> 读取；此处只改是否接管与会话范围。
          </p>
        </div>
      </div>

      <div className="space-y-4 rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-300">
          <Bot className="h-4 w-4 text-violet-400" />
          主机器人 IM（PMO / 通用）
        </div>
        <Toggle
          label="本机接管主机器人长连接（lark）"
          checked={larkEnabled}
          onChange={setLarkEnabled}
          disabled={saving}
        />
        <Toggle
          label="仅处理下方绑定的会话（白名单，推荐）"
          checked={larkExclusiveSessions}
          onChange={setLarkExclusiveSessions}
          disabled={saving || larkChatIds.length === 0}
        />
        <div>
          <label className="block text-xs text-slate-400 mb-2 font-medium">
            本机绑定的会话
          </label>
          <ChatIdListEditor
            ids={larkChatIds}
            onChange={(next) => {
              setLarkChatIds(next);
              if (next.length > 0 && !larkExclusiveSessions) {
                setLarkExclusiveSessions(true);
              }
            }}
            disabled={saving}
            exclusiveSessions={larkExclusiveSessions}
            emptyHint="尚未绑定会话。未配置 chat_id 时，本机作为默认节点处理长连接上的全部会话。"
          />
        </div>
      </div>

      <div className="space-y-4 rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-300">
          <Users className="h-4 w-4 text-amber-400" />
          HR 招聘机器人 IM
        </div>
        <Toggle
          label="本机接管 HR 机器人长连接（lark_hr）"
          checked={larkHrEnabled}
          onChange={setLarkHrEnabled}
          disabled={saving}
        />
        <Toggle
          label="仅处理下方绑定的 HR 会话（白名单，推荐）"
          checked={larkHrExclusiveSessions}
          onChange={setLarkHrExclusiveSessions}
          disabled={saving || larkHrChatIds.length === 0}
        />
        <p className="text-xs text-slate-500">
          与主机器人<strong className="text-slate-400">不同飞书应用</strong>时单独开启；同一应用则只开「主机器人」即可。
        </p>
        <div>
          <label className="block text-xs text-slate-400 mb-2 font-medium">
            HR 本机绑定的会话
          </label>
          <ChatIdListEditor
            ids={larkHrChatIds}
            onChange={(next) => {
              setLarkHrChatIds(next);
              if (next.length > 0 && !larkHrExclusiveSessions) {
                setLarkHrExclusiveSessions(true);
              }
            }}
            disabled={saving}
            exclusiveSessions={larkHrExclusiveSessions}
            emptyHint="尚未绑定 HR 会话。未配置 chat_id 时，本机作为默认节点处理全部 HR 会话。"
          />
        </div>
      </div>

      <div className="space-y-3 rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-300">
          <Table2 className="h-4 w-4 text-rose-400" />
          PMO 多维表变更（非聊天）
        </div>
        <Toggle
          label="本机接管 PMO 表变更长连接（lark_pmo_bitable）"
          checked={bitableEnabled}
          onChange={setBitableEnabled}
          disabled={saving}
        />
        <p className="text-xs text-slate-500">
          接收飞书多维表记录变更事件；凭证可回落到 PMO 技能配置{" "}
          <code className="text-cyan-300/90">pmo_bitable_watch.yaml</code>。
        </p>
      </div>

      <div>
        <label className="block text-xs text-slate-500 mb-1">开放平台 domain（飞书中国 / 国际版）</label>
        <select
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          disabled={saving}
          className="w-full max-w-md rounded border border-white/10 bg-black/30 px-3 py-2 text-sm text-slate-200"
        >
          <option value="https://open.feishu.cn">飞书中国 — open.feishu.cn</option>
          <option value="https://open.larksuite.com">Lark 国际 — open.larksuite.com</option>
        </select>
      </div>

      {error && <p className="text-sm text-rose-400">{error}</p>}
      {saved && (
        <p className="text-sm text-emerald-400">
          已保存。请<strong>完全退出并重启 Jachin Desktop</strong>，L3 侧车才会按新配置重建长连接。
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
            "bg-cyan-600/80 hover:bg-cyan-600 text-white disabled:opacity-50"
          )}
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          保存长连接配置
        </button>
        <button
          type="button"
          onClick={() => void openConfigDir()}
          className="inline-flex items-center gap-2 rounded-lg border border-white/15 px-3 py-2 text-xs text-slate-400 hover:text-slate-200"
        >
          <FolderOpen className="h-3.5 w-3.5" />
          打开配置目录
        </button>
        {configPath && (
          <span className="text-[10px] text-slate-600 font-mono truncate max-w-full">{configPath}</span>
        )}
      </div>
    </div>
  );
}
