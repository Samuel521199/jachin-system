"""流式 completion 的文本 delta 处理。

OpenAI 式接口通常每帧为**纯增量**；部分适配层每帧为**从开头累积的全文**。
错误地把累积帧当增量拼接会导致严重复读；错误地对纯增量做「去前缀」会截断正文。

策略：每个流式请求使用独立 ``StreamDeltaNormalizer``，用**前两帧**粗判模式；在 **inc** 模式下若收到「以当前快照为前缀的更长帧」则**升档为 cum**（补救误判）。
L3 与 ``core.llm_provider`` 的流式路径**默认**启用本归一化；DashScope 另见 ``core.litellm_stream_hints`` 的 ``incremental_output`` 提示。
环境变量 ``JACHIN_STREAM_DELTA_RAW=1`` 时强制按「每帧即增量」透传（调试用）。
旧环境变量 ``JACHIN_STREAM_DELTA_CUMULATIVE_FIX`` 与默认行为一致，无需再设。
"""
from __future__ import annotations

import os


def append_stream_delta(accumulated: str, delta: str) -> tuple[str, str]:
    """
    兼容旧调用：返回 (本轮应追加的片段, 新的已确认全文快照)。
    仅适用于已知「始终累积」的后端；新代码请用 StreamDeltaNormalizer。
    """
    if not delta:
        return "", accumulated
    if accumulated and delta.startswith(accumulated):
        return delta[len(accumulated) :], delta
    return delta, accumulated + delta


class StreamDeltaNormalizer:
    """
    每个 ``generate_response_stream`` / 单次 acompletion 流**新建一个实例**。
    ``feed`` 返回本轮应向下游展示的增量（可能为空字符串）。
    """

    __slots__ = ("_mode", "_snap")

    def __init__(self) -> None:
        self._mode: str | None = None  # "inc" | "cum"
        self._snap: str = ""

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        if os.environ.get("JACHIN_STREAM_DELTA_RAW", "").strip().lower() in ("1", "true", "yes"):
            self._snap += delta
            return delta

        if self._mode is None:
            if not self._snap:
                self._snap = delta
                return delta
            if self._snap == delta:
                return ""
            if delta.startswith(self._snap) and len(delta) > len(self._snap):
                self._mode = "cum"
                piece = delta[len(self._snap) :]
                self._snap = delta
                return piece
            self._mode = "inc"
            self._snap += delta
            return delta

        if self._mode == "cum":
            if self._snap == delta:
                return ""
            if self._snap and delta.startswith(self._snap):
                piece = delta[len(self._snap) :]
                self._snap = delta
                return piece
            self._snap = delta
            return delta

        # inc：通常为真增量；若后续某一帧突然变成「以当前已展示全文为前缀的累积包」（前两帧误判或混合适配层），切到 cum
        if delta == self._snap:
            return ""
        if self._snap and delta.startswith(self._snap) and len(delta) > len(self._snap):
            self._mode = "cum"
            piece = delta[len(self._snap) :]
            self._snap = delta
            return piece

        self._snap += delta
        return delta
