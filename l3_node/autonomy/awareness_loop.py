"""
AutonomousAwarenessLoop — 自主意识扫描循环（§5 Layer 2）

每 N 秒运行一次「意识扫描」：
1. 检查所有 PersistedIntent，触发到期的 cron/interval 任务；
   以及 condition 类型意图的内置条件评估（AJ）
2. 检查后台任务队列健康状态（异常连续失败告警）
3. 检查资源使用（磁盘空间 / token 日预算警戒）
4. 每日 23:55 触发 ProactiveReporter 生成日终总结并推送飞书
5. 失败意图自动重置（AK）：超过 JACHIN_INTENT_AUTORESET_HOURS 后恢复 active
6. Skill 自动进化检查（AY）：意图连续成功达阈值后触发 skill_evolver

环境变量
----------
JACHIN_AWARENESS_LOOP_DISABLE=1       关闭整个循环（不影响 PersistedIntent DB 读写）
JACHIN_AWARENESS_SCAN_INTERVAL=60     扫描间隔秒数（默认 60）
JACHIN_TOKEN_DAY_BUDGET=200000        Token 日消耗软上限（超过发告警）
JACHIN_CONDITION_INTENT_ENABLE=1      开启 condition 类意图内置条件评估（默认关，AJ）
JACHIN_CONDITION_LLM_EVAL=1           condition 评估 LLM fallback（默认关，AM）
JACHIN_INTENT_AUTORESET_HOURS=N       失败意图 N 小时后自动重置（默认 0=不自动重置，AK）
JACHIN_LEVEL3_HEALER_ENABLE=1         开启 Level 3 Experience RAG 自愈诊断（默认关，AQ）
JACHIN_SKILL_EVOLVE_ENABLE=1          开启 Skill 自动进化（默认关，AY）
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("autonomy.awareness_loop")


# ---------------------------------------------------------------------------
# 内置条件表达式评估器（AJ）
# 支持格式：
#   disk_free_gb < N          磁盘剩余空间（GB）
#   token_used > N            今日 token 绝对用量
#   token_used_pct > N        今日 token 使用百分比（0~100）
#   consecutive_failures:ID > N  某意图连续失败次数
# 运算符：< <= > >= ==
# ---------------------------------------------------------------------------

async def _evaluate_condition(condition: str, resource: "ResourceHealthReport | None" = None) -> bool:
    """
    评估条件表达式，返回 True 表示条件满足（应触发意图）。
    优先走内置规则（AJ），规则无法解析时若 JACHIN_CONDITION_LLM_EVAL=1 则调 LLM（AM）。
    失败时安全返回 False（不触发）。
    """
    expr = (condition or "").strip()
    if not expr:
        return False

    import re

    # 运算符支持
    def _cmp(val: float, op: str, rhs: float) -> bool:
        return {
            "<": val < rhs, "<=": val <= rhs,
            ">": val > rhs, ">=": val >= rhs,
            "==": val == rhs,
        }.get(op, False)

    # disk_free_gb <op> N
    m = re.fullmatch(r"disk_free_gb\s*(<=|>=|<|>|==)\s*([0-9.]+)", expr)
    if m:
        if resource is None:
            return False
        return _cmp(resource.disk_free_gb, m.group(1), float(m.group(2)))

    # token_used_pct <op> N
    m = re.fullmatch(r"token_used_pct\s*(<=|>=|<|>|==)\s*([0-9.]+)", expr)
    if m:
        if resource is None:
            return False
        pct = (resource.token_used_today / resource.token_budget * 100.0) if resource.token_budget else 0.0
        return _cmp(pct, m.group(1), float(m.group(2)))

    # token_used <op> N
    m = re.fullmatch(r"token_used\s*(<=|>=|<|>|==)\s*([0-9]+)", expr)
    if m:
        if resource is None:
            return False
        return _cmp(float(resource.token_used_today), m.group(1), float(m.group(2)))

    # consecutive_failures:intent_id <op> N
    m = re.fullmatch(r"consecutive_failures:(\S+)\s*(<=|>=|<|>|==)\s*([0-9]+)", expr)
    if m:
        intent_id = m.group(1)
        op = m.group(2)
        rhs = float(m.group(3))
        try:
            from l3_node.autonomy.intent_persister import get_intent_persister
            intent = get_intent_persister().get(intent_id)
            if intent is None:
                return False
            return _cmp(float(intent.consecutive_failures), op, rhs)
        except Exception:
            return False

    logger.debug("[AwarenessLoop] unrecognised condition expr: %r", expr)
    # AM：规则无法匹配时，尝试 LLM fallback 评估
    if _condition_llm_eval_enabled():
        return await _evaluate_condition_llm_fallback(expr, resource)
    return False


# ---------------------------------------------------------------------------
# AM — LLM 驱动条件评估（fallback 路径）
#
# 当内置规则无法解析条件表达式时，将系统状态摘要 + 条件交给轻量 LLM 做 yes/no 判断。
#
# 环境变量：
#   JACHIN_CONDITION_LLM_EVAL=1          开启 LLM fallback（默认关）
#   JACHIN_CONDITION_LLM_MODEL           使用的模型（默认跟随 LLM_MODEL 环境变量）
#   JACHIN_CONDITION_LLM_TIMEOUT         单次调用超时秒（默认 10）
# ---------------------------------------------------------------------------


def _condition_llm_eval_enabled() -> bool:
    return (os.environ.get("JACHIN_CONDITION_LLM_EVAL") or "").strip().lower() in (
        "1", "true", "yes"
    )


async def _evaluate_condition_llm_fallback(
    condition: str,
    resource: "ResourceHealthReport | None",
) -> bool:
    """
    AM — LLM 驱动条件评估。
    用最小 system_context + 条件文本调用 LLM，解析回复中的 yes/true/满足。
    失败/超时时安全返回 False（不触发）。
    """
    try:
        from l3_node.llm_client import LiteLLMEngine, SecurityContext

        model = (os.environ.get("JACHIN_CONDITION_LLM_MODEL") or os.environ.get("LLM_MODEL") or "").strip()
        timeout_sec = float(os.environ.get("JACHIN_CONDITION_LLM_TIMEOUT") or "10")

        ctx = SecurityContext()
        engine = LiteLLMEngine(ctx, model_name=model or "gpt-4o-mini", timeout=timeout_sec, max_attempts=1)

        # 构建系统状态摘要
        state_lines: list[str] = []
        if resource is not None:
            state_lines.append(f"disk_free_gb={resource.disk_free_gb:.1f}")
            state_lines.append(f"token_used_today={resource.token_used_today}")
            if resource.token_budget:
                pct = resource.token_used_today / resource.token_budget * 100.0
                state_lines.append(f"token_used_pct={pct:.1f}")
        state_summary = "; ".join(state_lines) if state_lines else "no resource data available"

        prompt = (
            f"System state: {state_summary}\n"
            f"Condition to evaluate: {condition}\n\n"
            "Based on the system state above, is the condition TRUE or FALSE?\n"
            "Reply with exactly one word: YES or NO."
        )
        messages = [{"role": "user", "content": prompt}]
        reply = await engine.generate_response(messages, temperature=0.0, max_tokens=8)
        answer = (reply or "").strip().lower()
        result = answer.startswith("yes") or answer.startswith("true") or "满足" in answer
        logger.info(
            "[AwarenessLoop][AM] LLM 条件评估 expr=%r → reply=%r → %s",
            condition, reply, result,
        )
        return result
    except Exception as e:
        logger.warning("[AwarenessLoop][AM] LLM 条件评估失败，回退 False: %s", e)
        return False


@dataclass
class ResourceHealthReport:
    disk_free_gb: float
    disk_warn: bool          # < 2 GB
    token_used_today: int
    token_budget: int
    token_warn: bool         # > 80% 预算


@dataclass
class AnomalyAlert:
    intent_id: str
    description: str
    consecutive_failures: int
    message: str
    action: str = ""   # intent.action 字段，供 AY 提取 skill_name


@dataclass
class AutonomousAction:
    action_type: str         # "fire_intent" | "resource_warn" | "daily_summary" | "anomaly"
    payload: dict[str, Any] = field(default_factory=dict)


class AutonomousAwarenessLoop:
    """
    每 N 秒运行一次意识扫描，驱动 PersistedIntent 自动执行与资源监控。

    使用方式（在 bootstrap.py 或 app startup 中）：
        loop = AutonomousAwarenessLoop()
        asyncio.create_task(loop.run_forever())
    """

    def __init__(self) -> None:
        self._running = False
        self._last_daily_summary_date: str = ""  # "YYYY-MM-DD"
        self._token_used_today: int = 0
        self._token_date: str = ""
        # AY — Skill 进化连续成功计数器: {skill_name: count}
        self._skill_success_counter: dict[str, int] = {}

    @property
    def scan_interval(self) -> int:
        raw = (os.environ.get("JACHIN_AWARENESS_SCAN_INTERVAL") or "60").strip()
        try:
            return max(10, int(raw))
        except ValueError:
            return 60

    @property
    def token_budget(self) -> int:
        raw = (os.environ.get("JACHIN_TOKEN_DAY_BUDGET") or "200000").strip()
        try:
            return max(1000, int(raw))
        except ValueError:
            return 200000

    def _is_disabled(self) -> bool:
        return (os.environ.get("JACHIN_AWARENESS_LOOP_DISABLE") or "").strip().lower() in ("1", "true", "yes")

    def _condition_intent_enabled(self) -> bool:
        return (os.environ.get("JACHIN_CONDITION_INTENT_ENABLE") or "").strip().lower() in ("1", "true", "yes")

    def _autoreset_hours(self) -> float:
        """失败意图自动重置的等待时长（小时）；0 = 不自动重置。"""
        raw = (os.environ.get("JACHIN_INTENT_AUTORESET_HOURS") or "0").strip()
        try:
            v = float(raw)
            return max(0.0, v)
        except ValueError:
            return 0.0

    async def run_forever(self) -> None:
        """持续运行，直到进程退出。应作为 asyncio.Task 启动。"""
        if self._is_disabled():
            logger.info("[AwarenessLoop] disabled (JACHIN_AWARENESS_LOOP_DISABLE=1)")
            return
        self._running = True
        logger.info("[AwarenessLoop] started, scan_interval=%ds", self.scan_interval)
        while self._running:
            try:
                actions = await self.scan_once()
                await self._dispatch_actions(actions)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[AwarenessLoop] scan error: %s", e)
            await asyncio.sleep(self.scan_interval)

    def stop(self) -> None:
        self._running = False

    async def scan_once(self) -> list[AutonomousAction]:
        """执行一次完整的意识扫描，返回需要执行的自主动作列表。"""
        actions: list[AutonomousAction] = []
        now = time.time()
        today_str = _date_str(now)

        # 1. 检查 PersistedIntent 到期触发（含 condition 评估 AJ，含自动重置 AK）
        rh = self._check_resource_health()  # 提前计算，供 condition 评估复用
        actions.extend(await self._check_intents(now, resource=rh))

        # 2. 资源检查（rh 已在上方计算，直接用）
        if rh.disk_warn:
            actions.append(AutonomousAction(
                action_type="resource_warn",
                payload={"type": "disk", "free_gb": rh.disk_free_gb,
                         "message": f"磁盘剩余空间不足 2GB（当前 {rh.disk_free_gb:.1f}GB），建议清理。"},
            ))
        if rh.token_warn:
            pct = int(rh.token_used_today / rh.token_budget * 100)
            actions.append(AutonomousAction(
                action_type="resource_warn",
                payload={"type": "token", "used": rh.token_used_today, "budget": rh.token_budget,
                         "message": f"Token 今日用量已达 {pct}%（{rh.token_used_today}/{rh.token_budget}）。"},
            ))

        # 3. 异常检测（连续失败的 Intent）
        anomalies = self._identify_anomalies()
        for anomaly in anomalies:
            actions.append(AutonomousAction(
                action_type="anomaly",
                payload={"intent_id": anomaly.intent_id,
                         "action": anomaly.action,       # AY: skill_name 提取需要 action 字段
                         "message": anomaly.message,
                         "failures": anomaly.consecutive_failures},
            ))

        # 4. 日终总结（每天 23:55 触发一次）
        if today_str != self._last_daily_summary_date:
            hour_min = _hour_min(now)
            if hour_min >= "23:55":
                actions.append(AutonomousAction(action_type="daily_summary", payload={"date": today_str}))
                self._last_daily_summary_date = today_str

        return actions

    async def _check_intents(
        self,
        now: float,
        resource: "ResourceHealthReport | None" = None,
    ) -> list[AutonomousAction]:
        """检查 interval / condition 类型的 PersistedIntent 是否应触发。

        AJ：condition 类型意图内置条件评估（需 JACHIN_CONDITION_INTENT_ENABLE=1）。
        AK：status=failed 的意图若超过 JACHIN_INTENT_AUTORESET_HOURS 则自动重置。
        """
        actions: list[AutonomousAction] = []
        autoreset_h = self._autoreset_hours()
        condition_enabled = self._condition_intent_enabled()
        try:
            from l3_node.autonomy.intent_persister import get_intent_persister
            persister = get_intent_persister()
            intents = persister.list_all(enabled_only=True)
            for intent in intents:
                # ── AK：失败意图自动重置 ────────────────────────────────────
                if intent.status == "failed" and autoreset_h > 0.0:
                    last_exec = intent.last_executed_at or intent.created_at
                    hours_since = (now - last_exec) / 3600.0
                    if hours_since >= autoreset_h:
                        try:
                            persister.autoreset_failed(intent.intent_id)
                            logger.info(
                                "[AwarenessLoop][AK] intent %s auto-reset after %.1fh failure window",
                                intent.intent_id, hours_since,
                            )
                            actions.append(AutonomousAction(
                                action_type="intent_autoreset",
                                payload={
                                    "intent_id": intent.intent_id,
                                    "description": intent.description,
                                    "hours_since_failure": round(hours_since, 1),
                                },
                            ))
                        except Exception as _err:
                            logger.debug("[AwarenessLoop][AK] autoreset error: %s", _err)
                    continue  # 本次扫描不触发，等下一轮

                if intent.status == "failed":
                    continue  # 无自动重置时跳过

                # ── interval：按时间间隔触发 ──────────────────────────────
                if intent.trigger.type == "interval" and intent.trigger.interval_sec:
                    last = intent.last_executed_at or intent.created_at
                    if now - last >= intent.trigger.interval_sec:
                        actions.append(AutonomousAction(
                            action_type="fire_intent",
                            payload={"intent_id": intent.intent_id,
                                     "action": intent.action,
                                     "description": intent.description},
                        ))

                # ── condition：内置条件表达式（AJ，需环境变量开启）────────
                elif intent.trigger.type == "condition" and condition_enabled:
                    cond_expr = (intent.trigger.condition or "").strip()
                    if cond_expr:
                        try:
                            satisfied = await _evaluate_condition(cond_expr, resource)
                        except Exception as _cerr:
                            logger.debug("[AwarenessLoop][AJ] condition eval error: %s", _cerr)
                            satisfied = False
                        if satisfied:
                            logger.info(
                                "[AwarenessLoop][AJ] condition intent %s satisfied: %r",
                                intent.intent_id, cond_expr,
                            )
                            actions.append(AutonomousAction(
                                action_type="fire_intent",
                                payload={"intent_id": intent.intent_id,
                                         "action": intent.action,
                                         "description": intent.description,
                                         "condition_expr": cond_expr},
                            ))
        except Exception as e:
            logger.debug("[AwarenessLoop] _check_intents error: %s", e)
        return actions

    def _check_resource_health(self) -> ResourceHealthReport:
        disk_free_gb = 99.0
        try:
            usage = shutil.disk_usage("/")
            disk_free_gb = usage.free / (1024 ** 3)
        except Exception:
            pass

        token_budget = self.token_budget
        token_used = self._get_token_used_today()
        return ResourceHealthReport(
            disk_free_gb=disk_free_gb,
            disk_warn=disk_free_gb < 2.0,
            token_used_today=token_used,
            token_budget=token_budget,
            token_warn=token_used > token_budget * 0.8,
        )

    def _get_token_used_today(self) -> int:
        today_str = _date_str(time.time())
        if self._token_date != today_str:
            self._token_used_today = 0
            self._token_date = today_str
        # 尝试从 llm_budget 模块读取今日使用量
        try:
            from l3_node.llm_budget import get_today_token_usage
            return get_today_token_usage()
        except Exception:
            return self._token_used_today

    def _identify_anomalies(self) -> list[AnomalyAlert]:
        alerts = []
        try:
            from l3_node.autonomy.intent_persister import get_intent_persister
            intents = get_intent_persister().list_all(enabled_only=True)
            for intent in intents:
                if intent.consecutive_failures >= 2:
                    alerts.append(AnomalyAlert(
                        intent_id=intent.intent_id,
                        description=intent.description,
                        consecutive_failures=intent.consecutive_failures,
                        action=getattr(intent, "action", ""),  # AY: 供 skill_name 提取
                        message=(
                            f"意图「{intent.description[:40]}」已连续失败 "
                            f"{intent.consecutive_failures} 次，建议检查。"
                        ),
                    ))
        except Exception as e:
            logger.debug("[AwarenessLoop] _identify_anomalies error: %s", e)
        return alerts

    async def _dispatch_actions(self, actions: list[AutonomousAction]) -> None:
        for action in actions:
            try:
                await self._execute_action(action)
            except Exception as e:
                logger.warning("[AwarenessLoop] dispatch action %s failed: %s", action.action_type, e)

    async def _execute_action(self, action: AutonomousAction) -> None:
        if action.action_type == "fire_intent":
            intent_id = action.payload.get("intent_id", "")
            task_desc = action.payload.get("action", "")
            description = action.payload.get("description", "")
            logger.info("[AwarenessLoop] firing interval intent %s: %s", intent_id, description[:60])
            try:
                from l3_node.agent_core import run_agent
                from l3_node.autonomy.intent_persister import get_intent_persister
                from l3_node.scheduled_global_registry import (
                    get_scheduled_l3_engine,
                    run_agent_implicit_attribution_for_scheduled,
                    scheduled_global_task_scope_async,
                )

                async with scheduled_global_task_scope_async(
                    "autonomy_intent",
                    intent_id,
                    title=description[:80],
                    extra_resource_tags=[f"intent:{intent_id[:48]}"],
                ) as sched_rid:
                    engine = get_scheduled_l3_engine()
                    result = await run_agent(
                        task_desc,
                        engine,
                        implicit_attribution=run_agent_implicit_attribution_for_scheduled(
                            "autonomy_intent",
                            intent_id,
                            parent_run_id=sched_rid,
                            base={"channel": "autonomy_intent"},
                        ),
                    )
                get_intent_persister().record_execution(
                    intent_id, success=True, result_summary=str(result)[:300]
                )
                # AY — Skill 自动进化：成功后检查是否达到进化阈值
                await self._try_skill_evolution_after_success(
                    intent_id=intent_id,
                    intent_action=task_desc,
                )
            except Exception as e:
                logger.error("[AwarenessLoop] intent %s failed: %s", intent_id, e)
                try:
                    from l3_node.autonomy.intent_persister import get_intent_persister
                    get_intent_persister().record_execution(
                        intent_id, success=False, result_summary=str(e)[:300]
                    )
                except Exception:
                    pass
                # AY — 失败时重置该 intent 关联 skill 的连续成功计数
                skill_name = _extract_skill_name_from_action(task_desc)
                if skill_name:
                    self._skill_success_counter.pop(skill_name, None)

        elif action.action_type == "resource_warn":
            msg = action.payload.get("message", "")
            logger.warning("[AwarenessLoop] [ResourceWarn] %s", msg)
            await _try_push_feishu(f"[系统资源告警] {msg}", urgency="warning")

        elif action.action_type == "anomaly":
            msg = action.payload.get("message", "")
            logger.warning("[AwarenessLoop] [Anomaly] %s", msg)
            await _try_push_feishu(f"[任务异常告警] {msg}", urgency="warning")
            # AQ — Level 3 自愈：Experience RAG 辅助诊断（JACHIN_LEVEL3_HEALER_ENABLE=1 时激活）
            try:
                from l3_node.autonomy.level3_healer import level3_healer_enabled, run_level3_healing
                if level3_healer_enabled():
                    intent_action = action.payload.get("action", "")
                    skill_name = _extract_skill_name_from_action(intent_action)
                    await run_level3_healing(
                        intent_id=action.payload.get("intent_id", ""),
                        intent_description=action.payload.get("message", ""),
                        consecutive_failures=int(action.payload.get("failures", 0)),
                        last_error=msg,
                        skill_name=skill_name,  # AY: 传入 skill_name 供 healing 路径预存候选
                    )
            except Exception as _l3e:
                logger.debug("[AwarenessLoop][AQ] level3 healer error: %s", _l3e)

        elif action.action_type == "daily_summary":
            date = action.payload.get("date", "")
            logger.info("[AwarenessLoop] triggering daily summary for %s", date)
            try:
                from l3_node.autonomy.proactive_reporter import ProactiveReporter
                summary = await ProactiveReporter().generate_daily_summary(date)
                await _try_push_feishu(summary, urgency="info")
            except Exception as e:
                logger.warning("[AwarenessLoop] daily summary failed: %s", e)

        elif action.action_type == "intent_autoreset":
            # AK：失败意图已重置，推送轻量自愈通知
            desc = action.payload.get("description", "")
            iid = action.payload.get("intent_id", "")
            hrs = action.payload.get("hours_since_failure", 0.0)
            msg = (
                f"[自愈通知] 意图「{desc[:60]}」（{iid[:12]}…）"
                f"已在连续失败后静待 {hrs:.1f}h，已自动重置为 active，将在下次扫描周期重试。"
            )
            logger.info("[AwarenessLoop][AK] %s", msg)
            await _try_push_feishu(msg, urgency="info")

    async def _try_skill_evolution_after_success(
        self, intent_id: str, intent_action: str
    ) -> None:
        """
        AY — 意图成功执行后，检查关联 Skill 是否达到进化阈值。
        两条路径：
          1. healing 路径（优先）：若 manifest 中存在 pending_evolution，立即消费
          2. proactive 路径：连续成功 N 次后基于 Experience RAG 进化
        """
        try:
            from l3_node.autonomy.skill_evolver import (
                evolve_enabled,
                run_skill_evolution_if_ready,
            )
            if not evolve_enabled():
                return

            skill_name = _extract_skill_name_from_action(intent_action)
            if not skill_name:
                return

            # 累计连续成功次数（用于 proactive 路径）
            prev = self._skill_success_counter.get(skill_name, 0)
            new_count = prev + 1
            self._skill_success_counter[skill_name] = new_count

            # 从 Experience RAG 拉最近的成功记录（proactive 路径备用）
            experience_records: list[dict] = []
            try:
                from l3_node.experience_memory import retrieve_experience
                experience_records = retrieve_experience(
                    query=intent_action[:200],
                    top_k=8,
                )
            except Exception:
                pass

            # run_skill_evolution_if_ready 内部会优先 consume_staged_evolution
            record = await run_skill_evolution_if_ready(
                skill_name=skill_name,
                consecutive_successes=new_count,
                last_experience_records=experience_records,
                trigger_reason=f"意图 {intent_id[:12]} 连续成功 {new_count} 次",
            )
            if record and record.status == "applied":
                logger.info(
                    "[AwarenessLoop][AY] Skill evolution applied: skill=%s trigger=%s ratio=%.1f%% evo_id=%s",
                    skill_name, record.trigger, record.change_ratio * 100, record.evolution_id[:8],
                )
                # 进化完成后重置计数器，防止连续触发
                self._skill_success_counter[skill_name] = 0
        except Exception as e:
            logger.debug("[AwarenessLoop][AY] skill evolution check error: %s", e)


# ---------------------------------------------------------------------------
# AY — Skill 自动进化辅助
# ---------------------------------------------------------------------------

def _extract_skill_name_from_action(action: str) -> str:
    """
    从意图 action 字符串中提取 Skill 名（目录名格式）。
    约定：action 中含 'SKILL:xxx' 标记，或 action 包含形如 com.jachin.xxx 的 Skill id。
    """
    import re
    if not action:
        return ""
    # 显式标记：SKILL:skill_name
    m = re.search(r"SKILL:([a-zA-Z0-9._\-]+)", action)
    if m:
        return m.group(1)
    # 隐式：反向域名形式的 Skill id（com.jachin.xxx）
    m = re.search(r"(com\.[a-zA-Z0-9._\-]+)", action)
    if m:
        return m.group(1)
    # 简单目录名形式（如 hr-recruitment, bi-analysis, youtube-summarizer）
    m = re.search(r"use_skill[（(]\s*['\"]?([a-zA-Z0-9_\-]+)['\"]?", action)
    if m:
        return m.group(1)
    return ""


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _date_str(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _hour_min(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


async def _try_push_feishu(text: str, *, urgency: str = "info") -> None:
    """尝试通过飞书 MCP 发送主动通知；失败则只打日志，不抛异常。"""
    try:
        from l3_node.channels.lark.im import send_text_to_default_chat
        await send_text_to_default_chat(f"[Jachin 自主通知·{urgency}]\n{text[:1000]}")
    except Exception as e:
        logger.debug("[AwarenessLoop] push feishu failed: %s", e)


# ---------------------------------------------------------------------------
# 单例 + 启动入口
# ---------------------------------------------------------------------------

_loop_instance: AutonomousAwarenessLoop | None = None


def get_awareness_loop() -> AutonomousAwarenessLoop:
    global _loop_instance
    if _loop_instance is None:
        _loop_instance = AutonomousAwarenessLoop()
    return _loop_instance


def start_awareness_loop_if_enabled() -> "asyncio.Task | None":
    """在 bootstrap 阶段调用，若未禁用则启动后台意识循环任务。"""
    loop = get_awareness_loop()
    if loop._is_disabled():
        return None
    task = asyncio.create_task(loop.run_forever(), name="awareness_loop")
    logger.info("[AwarenessLoop] background task created")
    return task
