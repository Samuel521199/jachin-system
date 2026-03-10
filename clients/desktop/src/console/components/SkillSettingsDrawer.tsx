/**
 * SkillSettingsDrawer - 技能设置侧边抽屉
 * 动态渲染 skill_registry 配置表单，支持 JD_template 多行、布尔 Switch
 * HR 透析镜：展示岗位 JD、简历路径、输出路径，每个路径有独立的绝对路径开关
 */

import React, { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Settings, Loader2, Save, X } from "lucide-react";
import { getSkillConfig, updateSkillConfig } from "../../lib/api";

const HR_SKILL_IDS = ["hr.analyzer", "hr-analyzer"];
/** HR 展示项：岗位 JD + 两个路径（各带独立绝对路径开关） */
const HR_FIELDS = [
  { key: "JD_template", label: "岗位 JD", isLongText: true },
  { key: "resume_input_dir", label: "简历路径", absoluteKey: "resume_input_dir_use_absolute" },
  { key: "output_dir", label: "输出分析结果的路径", absoluteKey: "output_dir_use_absolute" },
] as const;

function isHrSkill(skillId: string): boolean {
  const id = (skillId || "").toLowerCase();
  return HR_SKILL_IDS.some((h) => id.includes(h));
}

export interface SkillSettingsDrawerProps {
  skillId: string;
  skillName: string;
  onClose: () => void;
  onSuccess?: () => void;
}

export function SkillSettingsDrawer({
  skillId,
  skillName,
  onClose,
  onSuccess,
}: SkillSettingsDrawerProps) {
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [localConfig, setLocalConfig] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [successToast, setSuccessToast] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getSkillConfig(skillId);
      setConfig(data);
      if (isHrSkill(skillId)) {
        const filtered: Record<string, unknown> = {};
        for (const f of HR_FIELDS) {
          filtered[f.key] = f.key in data ? data[f.key] : (f.isLongText ? "" : "");
          if (f.absoluteKey) {
            filtered[f.absoluteKey] = data[f.absoluteKey] === true || data[f.absoluteKey] === "true";
          }
        }
        setLocalConfig(filtered);
      } else {
        setLocalConfig(data);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载配置失败");
    } finally {
      setLoading(false);
    }
  }, [skillId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await updateSkillConfig(skillId, localConfig);
      if (res.ok) {
        setConfig(localConfig);
        setSuccessToast(true);
        setTimeout(() => setSuccessToast(false), 3000);
        onSuccess?.();
      } else {
        setError(res.error ?? "保存失败");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const updateField = (key: string, value: unknown) => {
    setLocalConfig((prev) => ({ ...prev, [key]: value }));
  };

  const isHr = isHrSkill(skillId);
  const hasContent = isHr ? HR_FIELDS.length > 0 : Object.keys(localConfig).length > 0;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex justify-end"
      >
        <div
          className="absolute inset-0 bg-black/40 backdrop-blur-sm"
          onClick={onClose}
          aria-hidden
        />
        <motion.div
          initial={{ x: 400 }}
          animate={{ x: 0 }}
          exit={{ x: 400 }}
          transition={{ type: "spring", damping: 28, stiffness: 300 }}
          className="relative w-full max-w-md bg-slate-900/98 border-l border-rose-500/20 shadow-2xl overflow-y-auto"
        >
          <div className="sticky top-0 z-10 flex items-center justify-between px-4 py-3 border-b border-white/10 bg-slate-900/95">
            <div className="flex items-center gap-2">
              <Settings className="w-5 h-5 text-rose-400" />
              <h2 className="font-mono font-semibold text-white">技能设置</h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="p-2 rounded-lg hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="p-4">
            <p className="text-sm text-slate-400 mb-4">{skillName}</p>

            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="w-8 h-8 animate-spin text-rose-400" />
              </div>
            ) : !hasContent ? (
              <p className="text-slate-500 py-8 text-center font-mono text-sm">
                暂无可配置项，或该技能未声明 configs
              </p>
            ) : isHr ? (
              <div className="space-y-4">
                {HR_FIELDS.map((f) => {
                  const value = localConfig[f.key] ?? (f.isLongText ? "" : "");
                  const absoluteKey = f.absoluteKey;
                  const absValue = absoluteKey ? (localConfig[absoluteKey] === true) : false;
                  return (
                    <div key={f.key} className="space-y-1">
                      <label className="block text-xs font-mono text-slate-400 uppercase tracking-wider">
                        {f.label}
                      </label>
                      {f.isLongText ? (
                        <textarea
                          value={String(value)}
                          onChange={(e) => updateField(f.key, e.target.value)}
                          rows={6}
                          className="w-full px-3 py-2 rounded-lg bg-slate-800/80 border border-white/10 text-white font-mono text-sm placeholder-slate-500 focus:outline-none focus:border-rose-500/50 resize-y"
                          placeholder={`${f.label}...`}
                        />
                      ) : (
                        <div className="space-y-2">
                          <input
                            type="text"
                            value={String(value)}
                            onChange={(e) => updateField(f.key, e.target.value)}
                            className="w-full px-3 py-2 rounded-lg bg-slate-800/80 border border-white/10 text-white font-mono text-sm placeholder-slate-500 focus:outline-none focus:border-rose-500/50"
                            placeholder={`${f.label}...`}
                          />
                          {absoluteKey && (
                            <div className="flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => updateField(absoluteKey, !absValue)}
                                className={`relative w-11 h-6 rounded-full transition-colors ${
                                  absValue ? "bg-rose-500" : "bg-slate-600"
                                }`}
                              >
                                <span
                                  className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                                    absValue ? "left-6" : "left-1"
                                  }`}
                                />
                              </button>
                              <span className="text-xs text-slate-400">使用绝对路径</span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="space-y-4">
                {Object.entries(localConfig).map(([key, value]) => {
                  const isBool = typeof value === "boolean";
                  const isLongText =
                    typeof value === "string" &&
                    (key.toLowerCase().includes("template") ||
                      key.toLowerCase().includes("prompt") ||
                      value.length > 80);
                  return (
                    <div key={key} className="space-y-1">
                      <label className="block text-xs font-mono text-slate-400 uppercase tracking-wider">
                        {key}
                      </label>
                      {isBool ? (
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => updateField(key, !value)}
                            className={`relative w-11 h-6 rounded-full transition-colors ${
                              value ? "bg-rose-500" : "bg-slate-600"
                            }`}
                          >
                            <span
                              className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                                value ? "left-6" : "left-1"
                              }`}
                            />
                          </button>
                          <span className="text-sm text-slate-300">
                            {value ? "开" : "关"}
                          </span>
                        </div>
                      ) : isLongText ? (
                        <textarea
                          value={String(value ?? "")}
                          onChange={(e) => updateField(key, e.target.value)}
                          rows={6}
                          className="w-full px-3 py-2 rounded-lg bg-slate-800/80 border border-white/10 text-white font-mono text-sm placeholder-slate-500 focus:outline-none focus:border-rose-500/50 resize-y"
                          placeholder={`${key}...`}
                        />
                      ) : (
                        <input
                          type="text"
                          value={String(value ?? "")}
                          onChange={(e) => updateField(key, e.target.value)}
                          className="w-full px-3 py-2 rounded-lg bg-slate-800/80 border border-white/10 text-white font-mono text-sm placeholder-slate-500 focus:outline-none focus:border-rose-500/50"
                          placeholder={`${key}...`}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {error && (
              <p className="mt-4 text-sm text-amber-400 font-mono">{error}</p>
            )}

            {!loading && hasContent && (
              <button
                type="button"
                onClick={handleSave}
                disabled={saving}
                className="mt-6 w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-rose-600 hover:bg-rose-500 disabled:opacity-50 font-mono text-sm text-white transition-colors"
              >
                {saving ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Save className="w-4 h-4" />
                )}
                保存应用
              </button>
            )}
          </div>
        </motion.div>

        {/* 成功 Toast */}
        <AnimatePresence>
          {successToast && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[101] px-5 py-4 rounded-xl border-2 bg-emerald-900/95 border-emerald-400/60 shadow-[0_0_30px_rgba(52,211,153,0.35)] backdrop-blur-sm"
            >
              <p className="font-bold text-white tracking-wide" style={{ fontFamily: "Orbitron, sans-serif" }}>
                ⚡ 战略配置已覆写至 L2 内核！
              </p>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </AnimatePresence>
  );
}
