import {
  dispatchVoiceIntent,
  type VoiceDispatcherContext,
  type VoiceDispatcherDecision,
  type VoiceTaskRef,
} from "../src/voice/voiceIntentRouter";

type CheckResult = {
  name: string;
  passed: boolean;
  detail: string;
};

type Scenario = {
  name: string;
  text: string;
  ctx: VoiceDispatcherContext;
  expect: (decision: VoiceDispatcherDecision) => CheckResult[];
};

function check(name: string, passed: boolean, detail: string): CheckResult {
  return { name, passed, detail };
}

function compactDecision(d: VoiceDispatcherDecision): string {
  return JSON.stringify({
    tier: d.tier,
    intent: d.intent_class,
    lane: d.execution_lane,
    interrupt: d.interrupt_verdict,
    target: d.target_task_id,
    active: d.active_task_ids,
    title: d.task_title,
    confidence: d.confidence,
    ack: d.latency_masking.play_task_ack,
    orb: d.latency_masking.orb_mode,
    hud: d.latency_masking.hud_terminal,
    fast: d.router_hints.fast_lane,
    forceBackground: d.router_hints.force_background,
    acceptanceRound: d.router_hints.acceptance_round,
    preferDirectLlm: d.router_hints.prefer_direct_llm,
    injectTaskContext: d.router_hints.inject_task_context,
    injectLightTaskContext: d.router_hints.inject_light_task_context,
    notes: d.route_notes,
  });
}

function expectLongTaskSubmit(title: string): (d: VoiceDispatcherDecision) => CheckResult[] {
  return (d) => [
    check(`${title}: 归类为长任务`, d.tier === "LONG_TASK" && d.intent_class === "TASK_ASYNC", compactDecision(d)),
    check(`${title}: 进入后台提交车道`, d.execution_lane === "background_submit", compactDecision(d)),
    check(`${title}: 不走闲聊快路径`, d.router_hints.fast_lane === false && d.router_hints.prefer_direct_llm === false, compactDecision(d)),
    check(`${title}: 打开后台调度提示`, d.router_hints.force_background === true && d.router_hints.acceptance_round === true, compactDecision(d)),
    check(`${title}: 陪伴态不断聊反馈`, d.latency_masking.play_task_ack === true && d.latency_masking.orb_mode === "working", compactDecision(d)),
    check(`${title}: HUD 可展示后台终端`, d.latency_masking.hud_terminal === true, compactDecision(d)),
    check(`${title}: 不误触中断`, d.interrupt_verdict === "NONE" && d.target_task_id === null, compactDecision(d)),
  ];
}

function expectBackgroundControl(
  title: string,
  verdict: VoiceDispatcherDecision["interrupt_verdict"],
  targetTaskId: string,
): (d: VoiceDispatcherDecision) => CheckResult[] {
  return (d) => [
    check(`${title}: 归类为控制意图`, d.intent_class === "CONTROL", compactDecision(d)),
    check(`${title}: 控制目标稳定`, d.interrupt_verdict === verdict && d.target_task_id === targetTaskId, compactDecision(d)),
    check(`${title}: 控制走后台通道`, d.execution_lane === "background_control", compactDecision(d)),
    check(`${title}: 控制不重新提交长任务`, d.router_hints.force_background === false && d.execution_lane !== "background_submit", compactDecision(d)),
  ];
}

function expectChatWhileWorking(title: string, activeTaskId: string): (d: VoiceDispatcherDecision) => CheckResult[] {
  return (d) => [
    check(`${title}: 不提交新后台任务`, d.execution_lane !== "background_submit" && d.router_hints.force_background === false, compactDecision(d)),
    check(`${title}: 不中断当前任务`, d.interrupt_verdict !== "ABORT" && d.target_task_id !== activeTaskId, compactDecision(d)),
    check(`${title}: 保留任务上下文`, d.active_task_ids.includes(activeTaskId) && d.router_hints.inject_light_task_context === true, compactDecision(d)),
    check(`${title}: 仍可直接对话`, d.execution_lane === "direct_llm" || d.router_hints.prefer_direct_llm === true, compactDecision(d)),
  ];
}

function runScenario(scenario: Scenario): CheckResult[] {
  const started = performance.now();
  const decision = dispatchVoiceIntent(scenario.text, scenario.ctx);
  const costMs = performance.now() - started;
  console.log(`\n[SCENARIO] ${scenario.name}`);
  console.log(`input: ${scenario.text}`);
  console.log(`decision: ${JSON.stringify(decision, null, 2)}`);

  return [
    check(`${scenario.name}: 路由快速返回`, costMs < 50, `costMs=${costMs.toFixed(2)}`),
    ...scenario.expect(decision),
  ];
}

function printSummary(results: CheckResult[]): never {
  const failed = results.filter((r) => !r.passed);
  for (const r of results) {
    console.log(`[${r.passed ? "PASS" : "FAIL"}] ${r.name} -> ${r.detail}`);
  }
  console.log(`\nSummary: ${results.length - failed.length}/${results.length} checks passed.`);
  if (failed.length > 0) {
    console.log("Failed checks:");
    for (const f of failed) console.log(`- ${f.name}: ${f.detail}`);
    process.exit(1);
  }
  process.exit(0);
}

function main(): void {
  const activeLongTask: VoiceTaskRef = {
    id: "task-long-whitepaper",
    title: "whitepaper md 摘要报告",
  };
  const activeCtx: VoiceDispatcherContext = {
    activeTasks: [activeLongTask],
    lastFocusTaskId: activeLongTask.id,
  };

  const scenarios: Scenario[] = [
    {
      name: "提交长任务：批量文档摘要报告",
      text: "请帮我把 D 盘 project jachin system docs whitepaper 文件夹里所有 md 文档做摘要并生成报告，后台慢慢跑就行。",
      ctx: { activeTasks: [] },
      expect: (d) => [
        ...expectLongTaskSubmit("批量文档摘要报告")(d),
        check("批量文档摘要报告: 语音路径归一化", d.normalized_text.includes("D:\\project\\jachin-system-main\\docs\\whitepaper"), d.normalized_text),
        check("批量文档摘要报告: 使用稳定任务标题", d.task_title === "whitepaper md 摘要报告", compactDecision(d)),
      ],
    },
    {
      name: "提交长任务：全盘扫描清理",
      text: "帮我全盘扫描 C 盘和下载目录，把可疑的大文件、缓存和垃圾文件汇总成报告。",
      ctx: { activeTasks: [] },
      expect: expectLongTaskSubmit("全盘扫描清理"),
    },
    {
      name: "长任务运行中：查询进度不断聊",
      text: "现在进度怎么样了，做到哪一步了？",
      ctx: activeCtx,
      expect: expectBackgroundControl("查询进度", "STATUS", activeLongTask.id),
    },
    {
      name: "长任务运行中：修改要求不中断",
      text: "改成先摘要前三章，然后再继续处理剩下的文档。",
      ctx: activeCtx,
      expect: expectBackgroundControl("修改要求", "MODIFY", activeLongTask.id),
    },
    {
      name: "长任务运行中：用户闲聊不失联",
      text: "你先继续跑，我现在有点焦虑，陪我说两句。",
      ctx: activeCtx,
      expect: expectChatWhileWorking("用户闲聊不失联", activeLongTask.id),
    },
    {
      name: "长任务运行中：并行新长任务不误杀旧任务",
      text: "另外再帮我把所有会议纪要整理成行动项表格，也放后台跑。",
      ctx: activeCtx,
      expect: (d) => [
        ...expectLongTaskSubmit("并行新长任务")(d),
        check("并行新长任务: 保留旧任务列表", d.active_task_ids.includes(activeLongTask.id), compactDecision(d)),
      ],
    },
  ];

  const results = scenarios.flatMap(runScenario);
  printSummary(results);
}

main();