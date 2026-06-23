/**
 * 冒烟测试 — K11 统合平台 Playwright 冒烟（子进程，SSE 日志）
 * 手动脉冲的「多轮+间隔」与到点批跑解耦；目标 URL 与 CDP 由 .env/脚本默认处理。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FlaskConical } from "lucide-react";
import { cn } from "../../utils/cn";
import { useK11ScheduleLogLines } from "../K11ScheduleLogContext";
import {
  clearL3SkillsBaseUrlCache,
  getK11GameOpenSmokeStreamUrlAsync,
  getK11TongitsAutoplaySmokeStreamUrlAsync,
  getK11UnifiedSmokeStreamUrlAsync,
  getL3MonitorApiUrlAsync,
} from "../../lib/api";

const DEFAULT_SMOKE_HOUR_BEIJING = 9;
const DEFAULT_SMOKE_MINUTE_BEIJING = 0;

const DEFAULT_BIOS_HOUR_BEIJING = 10;
const DEFAULT_BIOS_MINUTE_BEIJING = 0;
const DEFAULT_BIOS_DAY_OFFSET = 1;

function biosDayOptionLabel(offset: number): string {
  if (offset <= 0) return "当日（维护日当天）";
  if (offset === 1) return "次日";
  return `第 ${offset + 1} 日`;
}

const BIOS_DAY_OFFSET_OPTIONS = Array.from({ length: 15 }, (_, i) => ({
  value: i,
  label: biosDayOptionLabel(i),
}));

export function K11UnifiedSmokeTest() {
  const scheduleLogLines = useK11ScheduleLogLines();
  const [runs, setRuns] = useState(4);
  const [intervalSec, setIntervalSec] = useState(30);
  const [verbose, setVerbose] = useState(true);
  const [noLark, setNoLark] = useState(false);
  const [hourBeijing, setHourBeijing] = useState(DEFAULT_SMOKE_HOUR_BEIJING);
  const [minuteBeijing, setMinuteBeijing] = useState(DEFAULT_SMOKE_MINUTE_BEIJING);
  /** true：每个整点小时的「分」与下方「分」对齐时批跑；false：仅每日在「时:分」批跑一次 */
  const [hourlyRecurring, setHourlyRecurring] = useState(false);
  const [running, setRunning] = useState(false);
  /** 仅 L3 端口探测中；勿与 running 混用，否则未起 L3 时长时间禁用「启动」像卡死。 */
  const [l3Probing, setL3Probing] = useState(false);
  /** 统合冒烟、游戏开门冒烟、P2/定时相关 SSE 行 */
  const [unifiedLogs, setUnifiedLogs] = useState<string[]>([]);
  const displayLogs = useMemo(
    () => [...scheduleLogLines, ...unifiedLogs],
    [scheduleLogLines, unifiedLogs]
  );
  const [doneOk, setDoneOk] = useState<boolean | null>(null);
  const [showNotify, setShowNotify] = useState(false);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [notifyMsg, setNotifyMsg] = useState("K11 统合冒烟已完成（退出码 0）");
  const [schedulerActive, setSchedulerActive] = useState(false);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [saveScheduleLoading, setSaveScheduleLoading] = useState(false);
  const [scheduleSaveBanner, setScheduleSaveBanner] = useState<string | null>(null);
  const scheduleSaveTimerRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  /** 发版公告生物钟（cron_thinker）：飞书邮箱/群 → 按维护日 + 偏移日 + 时刻登记统合冒烟 */
  const [biosEnabled, setBiosEnabled] = useState(true);
  const [biosDayOffset, setBiosDayOffset] = useState(DEFAULT_BIOS_DAY_OFFSET);
  const [biosHourBeijing, setBiosHourBeijing] = useState(DEFAULT_BIOS_HOUR_BEIJING);
  const [biosMinuteBeijing, setBiosMinuteBeijing] = useState(DEFAULT_BIOS_MINUTE_BEIJING);
  const [biosEnvReleaseSmoke, setBiosEnvReleaseSmoke] = useState(true);
  const [biosMailPollEnv, setBiosMailPollEnv] = useState(true);
  const [biosSaveLoading, setBiosSaveLoading] = useState(false);
  const [biosSaveBanner, setBiosSaveBanner] = useState<string | null>(null);
  const [biosEnableLoading, setBiosEnableLoading] = useState(false);
  const [biosSwitchLoading, setBiosSwitchLoading] = useState(false);
  const biosSaveTimerRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);
  /** 打包环境 WebView2 下 EventSource 可能因瞬时错误触发 onerror，勿据此结束任务；节流提示日志。 */
  const sseTransientWarnAtRef = useRef(0);

  useEffect(
    () => () => {
      esRef.current?.close();
      setRunning(false);
      setL3Probing(false);
      if (scheduleSaveTimerRef.current) window.clearTimeout(scheduleSaveTimerRef.current);
      if (biosSaveTimerRef.current) window.clearTimeout(biosSaveTimerRef.current);
    },
    []
  );

  const refreshScheduleStatus = useCallback(async () => {
    const apply = async (bypass: boolean) => {
      const url = await getL3MonitorApiUrlAsync("/api/v1/k11-unified-smoke/schedule/status", {
        bypassCache: bypass,
      });
      const res = await fetch(url);
      const data = (await res.json()) as {
        active?: boolean;
        hour_beijing?: number;
        minute_beijing?: number;
        runs?: number;
        interval_sec?: number;
        hourly_recurring?: boolean;
      };
      if (typeof data.active === "boolean") {
        setSchedulerActive(data.active);
      }
      if (typeof data.hour_beijing === "number") {
        setHourBeijing(data.hour_beijing);
      }
      if (typeof data.minute_beijing === "number") {
        setMinuteBeijing(data.minute_beijing);
      }
      if (typeof data.runs === "number") {
        setRuns(data.runs);
      }
      if (typeof data.interval_sec === "number") {
        setIntervalSec(data.interval_sec);
      }
      if (typeof data.hourly_recurring === "boolean") {
        setHourlyRecurring(data.hourly_recurring);
      }
    };
    try {
      await apply(false);
    } catch {
      try {
        clearL3SkillsBaseUrlCache();
        await apply(true);
      } catch {
        setSchedulerActive(false);
      }
    }
  }, []);

  const refreshBiosSettings = useCallback(async () => {
    const apply = async (bypass: boolean) => {
      const url = await getL3MonitorApiUrlAsync("/api/v1/cron-thinker/bios-settings", {
        bypassCache: bypass,
      });
      const res = await fetch(url);
      const data = (await res.json()) as {
        ok?: boolean;
        settings?: {
          enabled?: boolean;
          day_offset?: number;
          hour_beijing?: number;
          minute_beijing?: number;
        };
        env?: { release_smoke?: boolean; mail_poll?: boolean };
      };
      if (data.settings) {
        const s = data.settings;
        if (typeof s.enabled === "boolean") setBiosEnabled(s.enabled);
        if (typeof s.day_offset === "number")
          setBiosDayOffset(Math.max(0, Math.min(14, s.day_offset)));
        if (typeof s.hour_beijing === "number")
          setBiosHourBeijing(Math.max(0, Math.min(23, s.hour_beijing)));
        if (typeof s.minute_beijing === "number")
          setBiosMinuteBeijing(Math.max(0, Math.min(59, s.minute_beijing)));
      }
      if (data.env) {
        if (typeof data.env.release_smoke === "boolean") setBiosEnvReleaseSmoke(data.env.release_smoke);
        if (typeof data.env.mail_poll === "boolean") setBiosMailPollEnv(data.env.mail_poll);
      }
    };
    try {
      await apply(false);
    } catch {
      try {
        clearL3SkillsBaseUrlCache();
        await apply(true);
      } catch {
        /* keep local state */
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void refreshScheduleStatus();
    void refreshBiosSettings();
    const id = window.setInterval(() => {
      if (!cancelled) {
        void refreshScheduleStatus();
        void refreshBiosSettings();
      }
    }, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [refreshScheduleStatus, refreshBiosSettings]);

  const handleStop = useCallback(async () => {
    try {
      let url = await getL3MonitorApiUrlAsync("/api/v1/k11-unified-smoke/stop", { bypassCache: true });
      let res = await fetch(url, { method: "POST" });
      if (!res.ok) {
        clearL3SkillsBaseUrlCache();
        url = await getL3MonitorApiUrlAsync("/api/v1/k11-unified-smoke/stop", { bypassCache: true });
        res = await fetch(url, { method: "POST" });
      }
      const data = (await res.json().catch(() => ({}))) as {
        ok?: boolean;
        active_child?: boolean;
        message?: string;
      };
      const ok = res.ok && data?.ok !== false;
      setUnifiedLogs((prev) => [
        ...prev,
        !ok
          ? "> 停止请求失败（HTTP）。"
          : data.active_child === false
            ? `> ${String(data.message || "无运行中子进程；已记录停止或跳过排队轮次。")}`
            : "> 已发送停止信号（子进程将尽快退出）…",
      ]);
      if (ok) {
        esRef.current?.close();
        esRef.current = null;
        setRunning(false);
      }
    } catch {
      setUnifiedLogs((prev) => [...prev, "> 停止请求失败（网络）。"]);
    }
  }, [setUnifiedLogs]);

  const handleScheduleToggle = useCallback(
    async (enabled: boolean) => {
      setScheduleLoading(true);
      try {
        const r = Math.max(1, Math.min(99, Math.floor(runs) || 4));
        const i = Math.max(0, Math.min(3600, Math.floor(intervalSec) || 0));
        const h = Math.max(0, Math.min(23, Math.floor(hourBeijing) || DEFAULT_SMOKE_HOUR_BEIJING));
        const m = Math.max(0, Math.min(59, Math.floor(minuteBeijing) || 0));
        const body = JSON.stringify({
          enabled,
          hour_beijing: h,
          minute_beijing: m,
          runs: r,
          interval_sec: i,
          hourly_recurring: hourlyRecurring,
        });
        let url = await getL3MonitorApiUrlAsync("/api/v1/k11-unified-smoke/schedule/toggle", {
          bypassCache: true,
        });
        let res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
        });
        if (!res.ok) {
          clearL3SkillsBaseUrlCache();
          url = await getL3MonitorApiUrlAsync("/api/v1/k11-unified-smoke/schedule/toggle", {
            bypassCache: true,
          });
          res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
          });
        }
        const data = (await res.json().catch(() => ({}))) as {
          enabled?: boolean;
          active?: boolean;
        };
        const on =
          typeof data.enabled === "boolean"
            ? data.enabled
            : typeof data.active === "boolean"
              ? data.active
              : enabled;
        setSchedulerActive(on);
        void refreshScheduleStatus();
      } catch {
        setUnifiedLogs((prev) => [...prev, "> [WARN] 冒烟定时任务开关请求失败。"]);
      } finally {
        setScheduleLoading(false);
      }
    },
    [hourBeijing, hourlyRecurring, intervalSec, minuteBeijing, refreshScheduleStatus, runs]
  );

  const handleSaveSchedule = useCallback(async () => {
    setSaveScheduleLoading(true);
    setScheduleSaveBanner(null);
    try {
      const r = Math.max(1, Math.min(99, Math.floor(runs) || 4));
      const i = Math.max(0, Math.min(3600, Math.floor(intervalSec) || 0));
      const h = Math.max(0, Math.min(23, Math.floor(hourBeijing) || DEFAULT_SMOKE_HOUR_BEIJING));
      const m = Math.max(0, Math.min(59, Math.floor(minuteBeijing) || 0));
      const body = JSON.stringify({
        enabled: schedulerActive,
        hour_beijing: h,
        minute_beijing: m,
        runs: r,
        interval_sec: i,
        hourly_recurring: hourlyRecurring,
      });
      let url = await getL3MonitorApiUrlAsync("/api/v1/k11-unified-smoke/schedule/toggle", {
        bypassCache: true,
      });
      let res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (!res.ok) {
        clearL3SkillsBaseUrlCache();
        url = await getL3MonitorApiUrlAsync("/api/v1/k11-unified-smoke/schedule/toggle", {
          bypassCache: true,
        });
        res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
        });
      }
      const data = (await res.json().catch(() => ({}))) as { ok?: boolean; message?: string; active?: boolean };
      if (res.ok && data.ok !== false) {
        if (typeof data.active === "boolean") {
          setSchedulerActive(data.active);
        }
        const timeLabel = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
        const mm = String(m).padStart(2, "0");
        setScheduleSaveBanner(
          schedulerActive
            ? hourlyRecurring
              ? `已保存并生效：每小时北京 *:${mm} 到点各 1 轮。上方「执行轮数 / 间隔」仅影响「启动统合冒烟」手动脉冲。`
              : `已保存并生效：每日北京时间 ${timeLabel} 到点 1 轮。轮次/间隔仅用于手动脉冲。`
            : hourlyRecurring
              ? `已保存：每小时北京 *:${mm}（开关关则不跑；打开后按整点该分各触发 1 轮，与手动脉冲轮次无关）。`
              : `已保存定时：北京 ${timeLabel}（打开到点批跑开关后每触发 1 轮；与上方轮次/间隔无联动）。`
        );
        if (scheduleSaveTimerRef.current) window.clearTimeout(scheduleSaveTimerRef.current);
        scheduleSaveTimerRef.current = window.setTimeout(() => {
          setScheduleSaveBanner(null);
          scheduleSaveTimerRef.current = null;
        }, 5000);
        void refreshScheduleStatus();
      } else {
        setScheduleSaveBanner("保存失败，请重试或查看 L3 日志。");
      }
    } catch {
      setScheduleSaveBanner("保存失败（网络或 L3 未就绪）。");
    } finally {
      setSaveScheduleLoading(false);
    }
  }, [hourBeijing, hourlyRecurring, intervalSec, minuteBeijing, refreshScheduleStatus, runs, schedulerActive]);

  const postBiosSettings = useCallback(async (patch: Record<string, unknown>) => {
    const body = JSON.stringify({ ...patch, apply_runtime: true });
    let url = await getL3MonitorApiUrlAsync("/api/v1/cron-thinker/bios-settings", { bypassCache: true });
    let res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    if (!res.ok) {
      clearL3SkillsBaseUrlCache();
      url = await getL3MonitorApiUrlAsync("/api/v1/cron-thinker/bios-settings", { bypassCache: true });
      res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
    }
    const data = (await res.json().catch(() => ({}))) as {
      ok?: boolean;
      settings?: {
        enabled?: boolean;
        day_offset?: number;
        hour_beijing?: number;
        minute_beijing?: number;
      };
      runtime?: { ok?: boolean; reason?: string };
    };
    return { res, data };
  }, []);

  const handleSaveBiosTiming = useCallback(async () => {
    setBiosSaveLoading(true);
    setBiosSaveBanner(null);
    try {
      const d = Math.max(0, Math.min(14, Math.floor(biosDayOffset)));
      const h = Math.max(0, Math.min(23, Math.floor(biosHourBeijing)));
      const m = Math.max(0, Math.min(59, Math.floor(biosMinuteBeijing)));
      const { res, data } = await postBiosSettings({
        day_offset: d,
        hour_beijing: h,
        minute_beijing: m,
      });
      if (res.ok && data.ok !== false) {
        const timeLabel = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
        const dayLbl = biosDayOptionLabel(d);
        setBiosSaveBanner(
          `已保存生物钟定时：维护日后 ${dayLbl} · 北京时间 ${timeLabel}（已写入本机配置并重挂轮询）。`
        );
        void refreshBiosSettings();
      } else {
        setBiosSaveBanner("保存失败，请重试或查看 L3 日志。");
      }
    } catch {
      setBiosSaveBanner("保存失败（网络或 L3 未就绪）。");
    } finally {
      setBiosSaveLoading(false);
      if (biosSaveTimerRef.current) window.clearTimeout(biosSaveTimerRef.current);
      biosSaveTimerRef.current = window.setTimeout(() => {
        setBiosSaveBanner(null);
        biosSaveTimerRef.current = null;
      }, 6000);
    }
  }, [biosDayOffset, biosHourBeijing, biosMinuteBeijing, postBiosSettings, refreshBiosSettings]);

  const handleBiosSwitch = useCallback(
    async (enabled: boolean) => {
      setBiosSwitchLoading(true);
      setBiosSaveBanner(null);
      try {
        const { res, data } = await postBiosSettings({ enabled });
        if (res.ok && data.ok !== false) {
          setBiosEnabled(enabled);
          setBiosSaveBanner(
            enabled
              ? "生物钟已开启：识别到发版公告后将按已保存的「第几日 + 时刻」登记统合冒烟。"
              : "生物钟已关闭：不再根据公告登记冒烟（邮箱轮询已停止）。"
          );
          void refreshBiosSettings();
        } else {
          setBiosSaveBanner("切换失败，请重试。");
        }
      } catch {
        setBiosSaveBanner("切换失败（网络）。");
      } finally {
        setBiosSwitchLoading(false);
        if (biosSaveTimerRef.current) window.clearTimeout(biosSaveTimerRef.current);
        biosSaveTimerRef.current = window.setTimeout(() => {
          setBiosSaveBanner(null);
          biosSaveTimerRef.current = null;
        }, 5000);
      }
    },
    [postBiosSettings, refreshBiosSettings]
  );

  const handleBiosEnableNow = useCallback(async () => {
    setBiosEnableLoading(true);
    setBiosSaveBanner(null);
    try {
      const { res, data } = await postBiosSettings({ enabled: true });
      if (res.ok && data.ok !== false) {
        setBiosEnabled(true);
        const rt = data.runtime;
        const rtWarn =
          rt && rt.ok === false && rt.reason === "cron_thinker_release_smoke_disabled"
            ? " 提示：环境变量 JACHIN_CRON_THINKER_RELEASE_SMOKE 已关闭，整进程仍不会轮询公告。"
            : "";
        setBiosSaveBanner(`已执行「开启生物钟定时」并应用运行时。${rtWarn}`.trim());
        void refreshBiosSettings();
      } else {
        setBiosSaveBanner("开启失败，请重试。");
      }
    } catch {
      setBiosSaveBanner("开启失败（网络）。");
    } finally {
      setBiosEnableLoading(false);
      if (biosSaveTimerRef.current) window.clearTimeout(biosSaveTimerRef.current);
      biosSaveTimerRef.current = window.setTimeout(() => {
        setBiosSaveBanner(null);
        biosSaveTimerRef.current = null;
      }, 6000);
    }
  }, [postBiosSettings, refreshBiosSettings]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [displayLogs]);

  const connectSse = useCallback((streamUrl: string, initLogs: string[], notify: string) => {
      setNotifyMsg(notify);
      setUnifiedLogs(initLogs);
      setDoneOk(null);
      setExitCode(null);
      setShowNotify(false);
      const eventSource = new EventSource(streamUrl);
      esRef.current = eventSource;
      setRunning(true);

      eventSource.onmessage = (event: MessageEvent<string>) => {
        try {
          const data = JSON.parse(event.data) as Record<string, unknown>;
          if (data.type === "done") {
            const ok = data.ok === true;
            const cancelled = data.cancelled === true;
            setDoneOk(ok);
            if (typeof data.exit_code === "number") {
              setExitCode(data.exit_code);
            } else {
              setExitCode(null);
            }
            if (cancelled) {
              setUnifiedLogs((prev) => [...prev, "> █ 已中断。"]);
            }
            setUnifiedLogs((prev) => [...prev, `> █ 任务结束，退出码: ${String(data.exit_code ?? "?")}。`]);
            setRunning(false);
            eventSource.close();
            if (ok) {
              setShowNotify(true);
              window.setTimeout(() => setShowNotify(false), 7000);
            }
            return;
          }
          if (data.type === "error") {
            const msg = typeof data.message === "string" ? data.message : "未知错误";
            setUnifiedLogs((prev) => [...prev, `> [ERROR] ${msg}`]);
            setRunning(false);
            setDoneOk(false);
            eventSource.close();
            return;
          }
          if (typeof data.line === "string") {
            setUnifiedLogs((prev) => [...prev, `> ${data.line}`]);
          }
        } catch {
          /* ignore */
        }
      };

      eventSource.onerror = () => {
        // 规范下 onerror 在重连/抖动时也会触发；若此处 setRunning(false)+close 会导致「执行中但停止永灰」。
        // 正常结束以 onmessage 的 type done / error 为准；主动停止见 handleStop。
        if (eventSource.readyState === EventSource.CLOSED) {
          const hint = import.meta.env.DEV
            ? "请确认 L3 已启动且本页开发代理 /l3 可用。"
            : "请确认本机 L3 已跑起来；若仅 SSE 已断、子进程仍在，请点「停止」。";
          setUnifiedLogs((prev) => [...prev, `> [WARN] SSE 已结束（无自动重连）。${hint}`]);
          return;
        }
        const now = Date.now();
        if (now - sseTransientWarnAtRef.current > 8000) {
          sseTransientWarnAtRef.current = now;
          setUnifiedLogs((prev) => [
            ...prev,
            "> [INFO] SSE 连接异常或抖动（可能自动重试）；未收到任务结束信令前仍可点「停止」。",
          ]);
        }
      };
    },
    [setUnifiedLogs]
  );

  const handleStart = useCallback(() => {
    void (async () => {
      esRef.current?.close();
      const r = Math.max(1, Math.min(99, Math.floor(runs) || 4));
      const i = Math.max(0, Math.min(3600, Math.floor(intervalSec) || 0));
      setL3Probing(true);
      setDoneOk(null);
      setExitCode(null);
      setUnifiedLogs([
        "> 正在探测本机 L3 技能 HTTP（/api/v3/skills，多端口并行回退）…",
        "> 若失败将很快报 [ERROR]；本机 L3 未起时请运行 run_l3.bat 或主程序同目录 l3 侧车。",
      ]);
      let streamUrl: string;
      try {
        streamUrl = await getK11UnifiedSmokeStreamUrlAsync({
          verbose,
          noLarkReport: noLark,
          runs: r,
          interval: i,
        });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setUnifiedLogs([
          `> [ERROR] 连不上 L3：${msg}`,
          "> 另一台电脑若未起 L3：请与 Jachin 主程序同目录运行 run_l3.bat，或确认主程序未禁用自动拉起（勿随意设 JACHIN_SKIP_L3_SPAWN=1）。查看同目录 l3_debug.log。",
        ]);
        return;
      } finally {
        setL3Probing(false);
      }
      connectSse(
        streamUrl,
        [
          "> 初始化 K11 统合平台冒烟（Playwright）…",
          `> 计划: ${r} 轮, 轮次间隔: ${i} 秒（目标/ CDP 由 .env 与脚本默认站）`,
          "> 连接 L3 SSE 流…",
        ],
        "K11 统合冒烟已完成（退出码 0）"
      );
    })();
  }, [connectSse, intervalSec, noLark, runs, verbose]);

  const handleStartGameOpenSmoke = useCallback(() => {
    void (async () => {
      esRef.current?.close();
      setL3Probing(true);
      setDoneOk(null);
      setExitCode(null);
      setUnifiedLogs([
        "> 正在探测本机 L3 技能 HTTP（/api/v3/skills，多端口并行）…",
        "> 模式：游戏模块冒烟（test_k11_game_open_smoke.py -v）",
      ]);
      let streamUrl: string;
      try {
        streamUrl = await getK11GameOpenSmokeStreamUrlAsync({
          verbose,
          noLarkReport: noLark,
        });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setUnifiedLogs([
          `> [ERROR] 连不上 L3：${msg}`,
          "> 请确认 L3 已运行（同目录 run_l3.bat 或主程序随附侧车），并见 l3_debug.log。",
        ]);
        return;
      } finally {
        setL3Probing(false);
      }
      connectSse(
        streamUrl,
        [
          "> 初始化 K11 游戏模块冒烟…",
          "> 等效: python scripts/test_k11_game_open_smoke.py -v",
          "> 连接 L3 SSE 流…",
        ],
        "K11 游戏模块冒烟已完成（退出码 0）"
      );
    })();
  }, [connectSse, noLark, verbose]);

  const handleStartTongitsAutoplay = useCallback(() => {
    void (async () => {
      esRef.current?.close();
      setL3Probing(true);
      setDoneOk(null);
      setExitCode(null);
      setUnifiedLogs([
        "> 正在探测本机 L3 技能 HTTP…",
        "> 模式：Tongits 全自动打牌（test_k11_tongits_autoplay_smoke.py -v）",
        "> 将自动点击 Tongits King → Join → 等待进桌 → main_bot_loop 出牌 → 3016 结算 → Lark 卡片",
      ]);
      let streamUrl: string;
      try {
        streamUrl = await getK11TongitsAutoplaySmokeStreamUrlAsync({
          verbose,
          noLarkReport: noLark,
          roundWaitSec: 600,
        });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setUnifiedLogs([
          `> [ERROR] 连不上 L3：${msg}`,
          "> 请确认 L3 已运行，且调试 Chrome（CDP 9222）已打开 kalaroko 页。",
        ]);
        return;
      } finally {
        setL3Probing(false);
      }
      connectSse(
        streamUrl,
        [
          "> 初始化 Tongits 自动打牌冒烟…",
          "> 等效: python scripts/test_k11_tongits_autoplay_smoke.py -v",
          "> 连接 L3 SSE 流…",
        ],
        "Tongits 自动打牌已完成（退出码 0）"
      );
    })();
  }, [connectSse, noLark, verbose]);

  /** 探测 L3 与执行冒烟：合并后控制「启动」灰显，避免仅 running 在探测期误判为整轮执行中。 */
  const l3OrRun = l3Probing || running;

  return (
    <div className="relative flex h-full min-h-0 flex-col gap-5 p-6 text-cyan-300">
      {showNotify && (
        <div
          className={cn(
            "fixed right-6 top-20 z-[100] max-w-sm rounded-lg border px-4 py-3 text-sm backdrop-blur",
            "border-cyan-500/45 bg-black/90 text-cyan-100 shadow-[0_0_28px_rgba(34,211,238,0.18)]"
          )}
          role="status"
        >
          {notifyMsg}
        </div>
      )}

      <header className="flex flex-shrink-0 flex-wrap items-center gap-3 border-b border-cyan-500/15 pb-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-cyan-500/25 bg-cyan-500/10">
          <FlaskConical className="h-5 w-5 text-cyan-300" aria-hidden />
        </div>
        <div>
          <h2
            className="font-sci-fi text-lg font-semibold tracking-wide text-white"
            style={{ textShadow: "0 0 12px rgba(34, 211, 238, 0.45)" }}
          >
            ■ K11 统合平台冒烟
          </h2>
          <p className="text-xs text-cyan-700/90">
            统合：<span className="font-mono text-cyan-600/90">test_k11_unified_platform_smoke_playwright.py</span>
            {" · "}
            开门：
            <span className="font-mono text-cyan-600/90">test_k11_game_open_smoke.py</span>
            {" · "}
            Tongits：
            <span className="font-mono text-cyan-600/90">test_k11_tongits_autoplay_smoke.py</span>
          </p>
        </div>
      </header>

      <section className="flex flex-shrink-0 flex-col gap-4 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.05] p-4">
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-xs text-cyan-600/90">
            执行轮数 (Runs)
            <input
              type="number"
              min={1}
              max={99}
              value={runs}
              disabled={l3OrRun}
              onChange={(e) => setRuns(Number(e.target.value))}
              className="w-24 rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100 outline-none focus:border-cyan-400/60"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-cyan-600/90">
            轮次间隔 (秒)
            <input
              type="number"
              min={0}
              max={3600}
              value={intervalSec}
              disabled={l3OrRun}
              onChange={(e) => setIntervalSec(Number(e.target.value))}
              className="w-28 rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100 outline-none focus:border-cyan-400/60"
            />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-6 text-xs text-cyan-600/90">
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={verbose}
              disabled={l3OrRun}
              onChange={(e) => setVerbose(e.target.checked)}
              className="h-3.5 w-3.5"
            />
            详细输出 (-v)
          </label>
          <label className="flex cursor-pointer items-center gap-2" title="脚本 --no-lark-report">
            <input
              type="checkbox"
              checked={noLark}
              disabled={l3OrRun}
              onChange={(e) => setNoLark(e.target.checked)}
              className="h-3.5 w-3.5"
            />
            不推送飞书 (--no-lark-report)
          </label>
        </div>
        <div className="ml-auto flex flex-col items-stretch gap-3 sm:items-end">
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button
              type="button"
              disabled={l3OrRun}
              onClick={handleStart}
              className={cn(
                "rounded-lg px-5 py-2.5 text-sm font-bold transition-all",
                l3OrRun
                  ? "cursor-not-allowed bg-slate-800 text-slate-500"
                  : "bg-cyan-400 text-black shadow-[0_0_24px_rgba(34,211,238,0.35)] hover:bg-cyan-300"
              )}
            >
              {l3Probing ? "探测 L3 中…" : running ? "执行中…" : "🚀 启动统合冒烟"}
            </button>
            <button
              type="button"
              disabled={l3OrRun}
              onClick={handleStartGameOpenSmoke}
              className={cn(
                "rounded-lg border px-4 py-2.5 text-sm font-bold transition-all",
                l3OrRun
                  ? "cursor-not-allowed border-slate-700 bg-slate-900/50 text-slate-600"
                  : "border-violet-500/50 bg-violet-950/40 text-violet-100 shadow-[0_0_16px_rgba(139,92,246,0.2)] hover:bg-violet-900/50"
              )}
              title="python scripts/test_k11_game_open_smoke.py -v"
            >
              {l3Probing ? "探测 L3…" : "🎮 游戏模块开门冒烟"}
            </button>
            <button
              type="button"
              disabled={l3OrRun}
              onClick={handleStartTongitsAutoplay}
              className={cn(
                "rounded-lg border px-4 py-2.5 text-sm font-bold transition-all",
                l3OrRun
                  ? "cursor-not-allowed border-slate-700 bg-slate-900/50 text-slate-600"
                  : "border-amber-500/50 bg-amber-950/40 text-amber-100 shadow-[0_0_16px_rgba(245,158,11,0.2)] hover:bg-amber-900/50"
              )}
              title="python scripts/test_k11_tongits_autoplay_smoke.py -v（全自动进 Tongits + 打牌 + Lark 金币结算）"
            >
              {l3Probing ? "探测 L3…" : "🃏 Tongits 自动打牌"}
            </button>
            <button
              type="button"
              disabled={!(running || l3Probing)}
              onClick={() => void handleStop()}
              className={cn(
                "rounded-lg border px-4 py-2.5 text-sm font-semibold transition-all",
                running || l3Probing
                  ? "border-rose-500/50 bg-rose-950/40 text-rose-200 hover:bg-rose-900/50"
                  : "cursor-not-allowed border-slate-700 bg-slate-900/40 text-slate-600"
              )}
            >
              🛑 停止
            </button>
          </div>
        </div>
        <p className="text-[11px] leading-relaxed text-cyan-600/75">
          「启动统合冒烟」为<strong className="text-cyan-500/90">本页 SSE 流</strong>，仅按上列「执行轮数 / 轮次间隔」串行多轮。目标/ CDP 由
          <code className="text-cyan-500/85"> .env</code> 与脚本默认。L3
          侧<strong className="text-cyan-500/88">到点批跑</strong>在开启开关后，按你选的「每日一次」或「每小时定点」各
          <strong className="text-cyan-500/88">触发 1 轮</strong>，日志走「定时流」到本 MIND
          STREAM。进程侧日志也搜
          <code className="text-cyan-500/80">[k11_unified_smoke_scheduler]</code>。定时关飞书可设{" "}
          <code className="text-cyan-500/80">K11_SCHEDULED_SMOKE_NO_LARK=1</code>。
        </p>
        <div className="flex flex-col gap-3 border-t border-cyan-500/15 pt-4">
          <div className="text-xs font-medium text-cyan-500/90">⏲️ 每日统合冒烟（北京时间）</div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-xs text-cyan-600/90" title="「时」在每日一次模式下用；开「每小时定点」时仅「分」参与每整点对齐。到点每次触发 1 轮，不读上方手动脉冲轮次。">
              时
              <input
                type="number"
                min={0}
                max={23}
                value={hourBeijing}
                disabled={l3OrRun}
                onChange={(e) => setHourBeijing(Number(e.target.value))}
                className="w-16 rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100 outline-none focus:border-cyan-400/60"
              />
            </label>
            <span className="mb-2 text-cyan-500/50">:</span>
            <label className="flex flex-col gap-1 text-xs text-cyan-600/90" title="0–59 分">
              分
              <input
                type="number"
                min={0}
                max={59}
                value={minuteBeijing}
                disabled={l3OrRun}
                onChange={(e) => setMinuteBeijing(Number(e.target.value))}
                className="w-16 rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100 outline-none focus:border-cyan-400/60"
              />
            </label>
            <button
              type="button"
              disabled={l3OrRun || saveScheduleLoading}
              onClick={() => void handleSaveSchedule()}
              className={cn(
                "mb-0.5 rounded-lg border px-4 py-2 text-xs font-semibold transition",
                l3OrRun || saveScheduleLoading
                  ? "cursor-not-allowed border-slate-700 text-slate-500"
                  : "border-cyan-500/50 bg-cyan-950/50 text-cyan-100 shadow-[0_0_12px_rgba(34,211,238,0.15)] hover:border-cyan-400/50 hover:bg-cyan-900/40"
              )}
            >
              {saveScheduleLoading ? "保存中…" : "保存定时配置"}
            </button>
          </div>
          <label
            className="flex max-w-xl cursor-pointer items-start gap-2 text-xs text-cyan-600/90"
            title="开启：北京时每个整点小时的「分」与上方「分」一致时批跑（如分=0 则每整点一次）。关闭：仅在上方「时:分」每日批跑一次。「时」在关闭本项时参与每日时刻；开启本项时仅「分」参与每小时对齐。"
          >
            <input
              type="checkbox"
              className="mt-0.5 h-3.5 w-3.5 shrink-0"
              checked={hourlyRecurring}
              disabled={l3OrRun}
              onChange={(e) => setHourlyRecurring(e.target.checked)}
            />
            <span>
              <span className="font-medium text-cyan-500/90">每小时定点巡检</span>
              <span className="text-cyan-600/75">
                （开启后按「分」每个整点触发；不开启则仅在设定时刻每日一次）
              </span>
            </span>
          </label>
          {scheduleSaveBanner && (
            <p className="text-xs leading-relaxed text-emerald-400/90" role="status">
              {scheduleSaveBanner}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div
              className="flex items-center gap-2"
              title="已写入 L3 状态文件，保存定时后若开关为开会立即重排任务"
            >
              <span
                className={cn(
                  "inline-block h-2.5 w-2.5 shrink-0 rounded-full",
                  schedulerActive === true ? "bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.7)]" : "bg-slate-600"
                )}
                aria-hidden
              />
              <span className="text-xs text-cyan-600/85">
                {schedulerActive ? "Active" : "Inactive"} ·{" "}
                {hourlyRecurring ? (
                  <>
                    每小时 · 北京 *:{String(minuteBeijing).padStart(2, "0")}
                  </>
                ) : (
                  <>
                    北京 {`${String(hourBeijing).padStart(2, "0")}:${String(minuteBeijing).padStart(2, "0")}`}
                  </>
                )}
                {hourlyRecurring
                  ? "（每整点该分执行；关「每小时」后「时」用于每日一次）"
                  : "（填好时刻后点「保存定时配置」写入 L3；再打开开关即按该时刻批跑）"}
              </span>
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-cyan-600/90">
              <span className="select-none">开启每日到点批跑</span>
              <input
                type="checkbox"
                role="switch"
                className="h-4 w-9 cursor-pointer appearance-none rounded-full border border-cyan-500/40 bg-black/70 transition checked:bg-emerald-600/80 disabled:opacity-40"
                checked={schedulerActive}
                disabled={scheduleLoading}
                onChange={(e) => void handleScheduleToggle(e.target.checked)}
              />
            </label>
          </div>
        </div>

        <div className="flex flex-col gap-3 border-t border-amber-500/25 pt-4">
          <div className="text-xs font-medium text-amber-200/90">🧬 发版公告生物钟（cron_thinker）</div>
          <p className="text-[11px] leading-relaxed text-amber-700/90">
            与上方「每日统合冒烟」独立：在识别到「生产环境发版维护公告」后，按维护日与下列「第几日 + 时刻」登记一次统合冒烟（飞书邮箱轮询 /
            群消息 / HTTP 投递）。
          </p>
          <p className="text-[11px] leading-relaxed text-amber-600/85">
            公告正文「【维护时间】」里<strong>只解析日期</strong>（年-月-日）作为维护日；下面「时 / 分」才是<strong>统合冒烟执行时刻</strong>（北京），与公告里的维护窗口钟点无关。若维护日选「当日」而该时刻已过，请先调晚时刻并保存，等待邮箱下一轮轮询重试；若仍不触发，可检查本机{" "}
            <span className="font-mono text-amber-500/90">~/.jachin/data/cron_thinker_mail_seen.json</span> 是否曾错误标为已读。
          </p>
          {!biosEnvReleaseSmoke && (
            <p className="text-xs text-rose-400/90">
              当前环境变量 JACHIN_CRON_THINKER_RELEASE_SMOKE 已关闭，生物钟不会在 L3 进程内生效。
            </p>
          )}
          {!biosMailPollEnv && biosEnvReleaseSmoke && (
            <p className="text-xs text-amber-600/85">
              环境变量已关闭邮箱轮询（JACHIN_CRON_THINKER_MAIL_POLL）；仍可通过飞书群或 HTTP 触发登记。
            </p>
          )}
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-xs text-amber-600/85">
              维护日后第几天执行
              <select
                value={biosDayOffset}
                disabled={l3OrRun}
                onChange={(e) => setBiosDayOffset(Number(e.target.value))}
                className="min-w-[220px] rounded border border-amber-500/35 bg-black/60 px-2 py-1.5 text-amber-100 outline-none focus:border-amber-400/60"
              >
                {BIOS_DAY_OFFSET_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-amber-600/85">
              时（北京）
              <input
                type="number"
                min={0}
                max={23}
                value={biosHourBeijing}
                disabled={l3OrRun}
                onChange={(e) => setBiosHourBeijing(Number(e.target.value))}
                className="w-16 rounded border border-amber-500/35 bg-black/60 px-2 py-1.5 font-mono text-amber-100 outline-none focus:border-amber-400/60"
              />
            </label>
            <span className="mb-2 text-amber-500/50">:</span>
            <label className="flex flex-col gap-1 text-xs text-amber-600/85">
              分
              <input
                type="number"
                min={0}
                max={59}
                value={biosMinuteBeijing}
                disabled={l3OrRun}
                onChange={(e) => setBiosMinuteBeijing(Number(e.target.value))}
                className="w-16 rounded border border-amber-500/35 bg-black/60 px-2 py-1.5 font-mono text-amber-100 outline-none focus:border-amber-400/60"
              />
            </label>
            <button
              type="button"
              disabled={l3OrRun || biosSaveLoading}
              onClick={() => void handleSaveBiosTiming()}
              className={cn(
                "mb-0.5 rounded-lg border px-4 py-2 text-xs font-semibold transition",
                l3OrRun || biosSaveLoading
                  ? "cursor-not-allowed border-slate-700 text-slate-500"
                  : "border-amber-500/50 bg-amber-950/40 text-amber-100 hover:border-amber-400/50 hover:bg-amber-900/35"
              )}
            >
              {biosSaveLoading ? "保存中…" : "保存生物钟定时"}
            </button>
            <button
              type="button"
              disabled={l3OrRun || biosEnableLoading}
              onClick={() => void handleBiosEnableNow()}
              title="打开生物钟并立即重挂邮箱轮询（与总开关一致）"
              className={cn(
                "mb-0.5 rounded-lg px-4 py-2 text-xs font-semibold transition",
                l3OrRun || biosEnableLoading
                  ? "cursor-not-allowed bg-slate-800 text-slate-500"
                  : "bg-amber-500/90 text-black hover:bg-amber-400"
              )}
            >
              {biosEnableLoading ? "开启中…" : "开启生物钟定时"}
            </button>
          </div>
          {biosSaveBanner && (
            <p className="text-xs leading-relaxed text-emerald-400/90" role="status">
              {biosSaveBanner}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="max-w-xl text-xs text-amber-600/85">
              关闭开关后：不再轮询邮箱，也不再根据公告登记新的冒烟任务（已登记的到期仍会执行）。
            </span>
            <label className="flex shrink-0 cursor-pointer items-center gap-2 text-xs text-amber-600/90">
              <span className="select-none">生物钟总开关</span>
              <input
                type="checkbox"
                role="switch"
                className="h-4 w-9 cursor-pointer appearance-none rounded-full border border-amber-500/40 bg-black/70 transition checked:bg-amber-600/80 disabled:opacity-40"
                checked={biosEnabled}
                disabled={biosSwitchLoading}
                onChange={(e) => void handleBiosSwitch(e.target.checked)}
              />
            </label>
          </div>
        </div>
      </section>

      <section className="flex min-h-0 flex-1 flex-col gap-2">
        <div className="text-[11px] font-medium uppercase tracking-[0.2em] text-cyan-600/75">
          # MIND STREAM :: K11 SMOKE（统合 / 开门 / 定时）
        </div>
        <div
          className={cn(
            "flex min-h-[280px] flex-1 flex-col overflow-y-auto rounded-lg border border-cyan-500/25 p-4",
            "bg-[#0a0e17] font-mono text-sm leading-relaxed text-cyan-300",
            "[text-shadow:0_0_10px_rgba(6,182,212,0.12)]"
          )}
        >
          {displayLogs.length === 0 && !l3OrRun && (
            <div className="text-cyan-700/65">
              本页为<strong className="text-cyan-500/85">统合全量</strong>、<strong className="text-cyan-500/85">游戏开门冒烟</strong>
              与「定时到点流」的合并区（上方先行）。点「启动统合冒烟」走手动脉冲 SSE；开「到点批跑」后，开关/到点/脚本输出会经定时
              SSE 与上方说明一并出现在此。
            </div>
          )}
          {displayLogs.length === 0 && l3OrRun && (
            <div className="text-cyan-600/80">
              已请求（探测 L3 或已连 SSE）… 若长期无新行：本机 L3 未起、或 127.0.0.1:1899x 被拦截；侧车未随主程序启动时检查 run_l3.bat / 勿设
              JACHIN_SKIP_L3_SPAWN=1。
            </div>
          )}
          {displayLogs.map((log, index) => (
            <div key={`${index}-${log.slice(0, 48)}`} className="mb-1 break-words whitespace-pre-wrap">
              {log}
            </div>
          ))}
          <div ref={logsEndRef} />
        </div>
      </section>

      {exitCode != null && doneOk === false && (
        <p className="flex-shrink-0 text-sm text-rose-400/95">
          本次任务未通过，退出码 {exitCode}。请根据上方日志与 Playwright/目标站状态排查。
        </p>
      )}
    </div>
  );
}
