"""
Jachin Voice CLI — 听觉与发声器官入口

启动全息感官总线，运行麦克风循环。
入口: python -m core.voice_cli
"""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

console = Console(
    theme=Theme(
        {
            "cyan": "#22d3ee",
            "purple": "#a78bfa",
            "green": "#22c55e",
            "magenta": "#d946ef",
            "dim": "dim",
        }
    )
)

BANNER = """
[magenta]  ██╗ █████╗  ██████╗██╗  ██╗██╗███╗   ██╗[/magenta]
[magenta]  ██║██╔══██╗██╔════╝██║  ██║██║████╗  ██║[/magenta]
[magenta]  ██║███████║██║     ███████║██║██╔██╗ ██║[/magenta]
[cyan]  ██║██╔══██║██║     ██╔══██║██║██║╚██╗██║[/cyan]
[cyan]  ██║██║  ██║╚██████╗██║  ██║██║██║ ╚████║[/cyan]
[cyan]  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝[/cyan]
[dim]     V O I C E   ·  听觉与喉咙  ·  全息感官[/dim]
"""


async def _main() -> None:
    import os
    from core.event_bus import get_bus, start_omni_consumer, set_omni_step_callback
    from core.senses.voice_organ import JachinVoiceInterface

    if not os.environ.get("OPENAI_API_KEY"):
        console.print("[yellow]⚠ 未设置 OPENAI_API_KEY，Whisper STT 将不可用。请配置后重试。[/yellow]")
        console.print("[dim]  edge-tts 无需 API Key。[/dim]")
        console.print()

    console.print(BANNER)
    console.print()
    console.print(Panel(
        "[green]🎙️ Voice Interface Online[/green]\n"
        "听觉与发声器官已就绪，请直接对麦克风下达指令。",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()

    bus = get_bus()
    set_omni_step_callback(None)  # Voice 模式不打印 ReAct 步骤到控制台
    start_omni_consumer()

    voice = JachinVoiceInterface(bus)
    try:
        await voice.run_voice_loop()
    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]🛑 语音接口已关闭，神经连接已断开。[/yellow]")
        sys.exit(0)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
